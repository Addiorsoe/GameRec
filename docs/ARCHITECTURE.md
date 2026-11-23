# GameRec 架構規劃

> 目標：一個 Windows 桌面工具，指定一個目標程式後，**同時**記錄
> ① 記憶體位址數值變化、② 該程式的網路封包、③ 視窗畫面錄影、④ 使用者鍵鼠輸入與標記，
> 所有資料使用**同一個時間軸**，結束後一鍵匯出成單一 `.zip` 封包交給 Claude 分析。
> 分析結果日後用來實作「自動巡路」。把 Claude 直接接到電腦上是後續階段，不在本版範圍。

> ⚠️ 線上遊戲多數有反外掛（讀取記憶體、側錄封包都可能被偵測），且通常違反服務條款，
> 可能導致封號。請優先在單機遊戲、自架私服或測試帳號上使用。

---

## 1. 整體流程

```
 ┌──────────── GUI (tkinter) / CLI ────────────┐
 │ 選程式 → 設定監看位址 → 開始 → 加標記 → 停止 → 匯出 │
 └───────────────┬────────────────────────────┘
                 │  Session（共用時鐘 SessionClock）
     ┌───────────┼───────────┬────────────┬────────────┐
     ▼           ▼           ▼            ▼            ▼
 MemoryWatcher PacketCapture ScreenRecorder InputLogger  Markers
 (pymem 輪詢)  (scapy+Npcap) (mss+OpenCV)  (pynput)     (熱鍵/按鈕)
     │           │           │            │            │
     ▼           ▼           ▼            ▼            ▼
 memory/      network/     video/       input/       markers.jsonl
 samples.csv  capture.pcap screen.mp4   events.jsonl
              packets.jsonl frames.csv
                 └──────────── exporter → session_XXXX.zip（含 manifest.json）
```

每個記錄器都是一個 `Recorder` 子類（`start(session)` / `stop()`），在自己的執行緒中工作，
寫入 session 資料夾。任何一個記錄器失敗（例如沒裝 Npcap）不會中止其他記錄器，錯誤寫入
`logs/recorder.log` 並標記在 manifest 的 `streams[].status`。

## 2. 時間同步

- `SessionClock` 在開始時記下 `time.perf_counter_ns()` 與 UTC 牆鐘時間。
- 所有資料都寫 `t_ns` ＝ 距 session 開始的奈秒數（單調遞增、不受系統調時影響）。
- 影片每一幀的 `t_ns` 寫進 `video/frames.csv`，因此影片幀可以精準對到記憶體樣本與封包
  （不依賴固定 FPS）。
- 封包另外保留 pcap 原生時間戳（epoch），manifest 記錄換算基準，可在 Wireshark 開啟。

## 3. 模組

| 模組 | 套件 | 職責 |
|---|---|---|
| `gamerec/clock.py` | 標準庫 | 共用時鐘 |
| `gamerec/process.py` | psutil, pywin32 | 列出程式、找主視窗與視窗座標、取得程式的連線埠 |
| `gamerec/memory/scanner.py` | pymem | 數值掃描（首次掃描 / 再掃描縮小範圍），找出座標、HP 等位址 |
| `gamerec/memory/watcher.py` | pymem | 依監看清單以固定頻率（預設 30Hz）讀值，支援「模組+偏移」與指標鏈 |
| `gamerec/net/capture.py` | scapy + Npcap | 抓網卡封包，依目標程式目前的本機埠動態過濾，寫 pcap + JSONL 索引 |
| `gamerec/video/recorder.py` | mss, opencv-python | 依視窗位置截圖錄成 mp4，每幀寫時間戳 |
| `gamerec/inputs/logger.py` | pynput | 記錄鍵盤/滑鼠事件（巡路分析需要知道玩家按了什麼） |
| `gamerec/session.py` | — | 建立 session 資料夾、管理所有記錄器、標記 |
| `gamerec/export.py` | zipfile | 產生 manifest、打包 zip |
| `gamerec/ui/app.py` | tkinter | 圖形介面 |
| `gamerec/cli.py` | argparse | 命令列（方便日後讓 Claude 遠端呼叫） |

所有 Windows 專屬套件都在使用時才 import，核心邏輯（時鐘、session、匯出）可在任何平台測試。

### 3.1 記憶體監看清單格式（`watchlist.json`）

```json
[
  {"name": "player_x", "type": "float", "address": "0x1A2B3C40"},
  {"name": "player_y", "type": "float", "module": "game.exe", "offsets": ["0x1F00A0", "0x10", "0x48"]},
  {"name": "hp",       "type": "int32", "module": "game.exe", "offset": "0x2233F0"}
]
```

- `address`：絕對位址（每次重開遊戲可能變）。
- `module` + `offset`：模組基址 + 偏移（靜態位址）。
- `module` + `offsets`：指標鏈，`[base_offset, off1, off2...]`，最後一個偏移是值本身。
- `type`：`int8/16/32/64`, `uint8/16/32/64`, `float`, `double`, `bytes:N`。

### 3.2 封包過濾

Windows 上無法直接「依 PID 抓封包」。做法：每 1 秒用 `psutil` 取得目標程式所有
TCP/UDP 連線的本機埠，抓網卡全部流量，只保留來源或目的埠在該集合中的封包。
（之後若要攔截/修改封包，可換成 WinDivert / pydivert，但本工具只做被動記錄。）

## 4. 匯出封包規格（交給 Claude 的檔案）

檔名：`session_YYYYMMDD_HHMMSS_<程式名>.zip`

```
manifest.json           # 本次記錄的總說明（見下）
markers.jsonl           # 使用者標記：{"t_ns":..., "label":"開始往北走"}
memory/watchlist.json   # 監看清單（含解析出的實際位址）
memory/samples.csv      # t_ns,name,value   （長格式，一行一個樣本）
network/capture.pcap    # 原始封包，可用 Wireshark 開
network/packets.jsonl   # {"t_ns","dir":"out|in","proto","src","sport","dst","dport","len","payload_hex"}
video/screen.mp4        # 視窗錄影
video/frames.csv        # frame,t_ns
input/events.jsonl      # {"t_ns","kind":"key_down|key_up|mouse_move|mouse_click|scroll", ...}
logs/recorder.log
```

`manifest.json` 範例：

```json
{
  "format": "gamerec-session",
  "format_version": 1,
  "tool_version": "0.1.0",
  "created_utc": "2026-11-23T21:40:00Z",
  "duration_s": 182.4,
  "clock": {"start_utc": "...", "start_epoch_ns": 0, "unit": "t_ns = ns since session start"},
  "target": {"pid": 1234, "name": "game.exe", "exe": "C:/Games/game.exe", "window_title": "...", "window_rect": [0,0,1280,720]},
  "streams": [
    {"kind": "memory",  "status": "ok", "files": ["memory/samples.csv"], "rate_hz": 30, "count": 5472},
    {"kind": "network", "status": "ok", "files": ["network/capture.pcap", "network/packets.jsonl"], "count": 812},
    {"kind": "video",   "status": "ok", "files": ["video/screen.mp4", "video/frames.csv"], "fps": 15, "count": 2736},
    {"kind": "input",   "status": "ok", "files": ["input/events.jsonl"], "count": 640}
  ],
  "notes": "使用者在匯出前輸入的備註"
}
```

設計原則：純文字（CSV/JSONL）優先，Claude 能直接讀；影片控制在 15 FPS、可選縮放，
讓 zip 大小合理（約 1～3 MB/分鐘）。

## 5. 介面（tkinter）

1. **目標**：程式下拉清單（名稱 + PID）＋重新整理；自動抓主視窗。
2. **記憶體**：監看清單表格（新增/刪除/匯入/匯出 JSON）＋「掃描器」分頁：
   輸入數值 → 首次掃描 → 變動後再掃描 → 把結果加入監看清單。
3. **錄製設定**：勾選要啟用的記錄器、FPS、取樣頻率、網卡選擇。
4. **控制列**：開始 / 停止 / 加標記（熱鍵 F9）/ 匯出 zip；即時顯示各串流筆數。

## 6. 打包

- `PyInstaller` 單一檔案、無主控台模式（`scripts/build_exe.bat`），產出可直接雙擊的 `dist/GameRec.exe`；GitHub Actions 在 windows-latest 自動建置並發佈到 Releases。
- 需要系統管理員權限（讀其他程式記憶體、抓封包），exe 內嵌 `uac_admin` manifest。
- 使用者需自行安裝 **Npcap**（授權不允許隨附，安裝時勾選 WinPcap 相容模式）；GUI 啟動時偵測，沒裝會跳提示並提供下載按鈕，封包串流自動停用，其他照常。

## 7. 開發階段

| 階段 | 內容 |
|---|---|
| **M1（本次骨架）** | 時鐘、session、匯出、四個記錄器的基本實作、tkinter 介面、CLI、測試、打包腳本 |
| M2 | 記憶體掃描器 UI、指標鏈驗證、影片縮放/區域選取、匯出前預覽 |
| M3 | 分析工具：時間軸對齊檢視、座標軌跡圖、封包欄位與座標相關性比對 |
| M4 | 自動巡路：由座標位址 + 移動輸入建立地圖/路點，閉環控制移動 |
| M5 | 讓 Claude 透過 CLI 直接連到電腦操作（後續再做） |
