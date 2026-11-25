# GameRec

指定一個 Windows 程式，**同時**記錄它的：

- 🧠 **記憶體**：監看指定位址（絕對位址 / 模組+偏移 / 指標鏈），30Hz 取樣
- 🌐 **封包**：只保留該程式的 TCP/UDP 連線（pcap + JSONL）
- 🎬 **畫面**：錄下遊戲視窗，每幀都有時間戳
- ⌨️ **鍵鼠輸入**：玩家按了什麼（巡路分析用）
- ⏯ **一鍵錄製**：遊戲中按 F9 開始、再按 F9 停止並匯出；一段一個動作，每段一個 zip
- 🚩 **標記**：錄影中按 F10 加註「現在開始往北走」之類的標籤

全部用同一條時間軸，停止後匯出成一個 `session_*.zip`，直接交給 Claude 分析。
詳細設計與匯出格式見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

> ⚠️ 讀取其他程式記憶體與側錄封包，在多數線上遊戲會被反外掛偵測並違反服務條款，可能導致封號。
> 請在單機遊戲、自架私服或測試帳號上使用。

## 下載與執行（一般使用）

1. 到 [Releases](https://github.com/Addiorsoe/gamerec/releases) 下載最新的 `GameRec.exe`
   （或到 [Actions](https://github.com/Addiorsoe/gamerec/actions) 點最新一次成功的建置，下載 `GameRec-exe`）
2. **雙擊 `GameRec.exe`**，Windows 會詢問系統管理員權限，按「是」
3. 程式啟動時會**自動檢查環境**，有缺少的項目會跳出「環境檢查」視窗，每一項旁邊都有一鍵修復按鈕：

   | 項目 | 沒通過時 |
   |---|---|
   | Windows 10/11、64 位元 | 顯示原因（無法自動修復） |
   | 系統管理員權限 | 「以管理員重新啟動」 |
   | Npcap 封包驅動 | 「一鍵下載安裝」：從官方 npcap.com 下載並開啟安裝程式，安裝時勾選 **WinPcap API-compatible Mode** |
   | 新版本 | 從本 repo 的 GitHub Releases 檢查，一鍵下載新版 exe |

   之後可隨時按主畫面的「環境檢查 / 更新」再開啟。Npcap 只有抓封包需要，沒裝也能監看記憶體和錄影。

不需要安裝 Python，也不需要打任何指令。

### 操作流程

1. 切到遊戲視窗按 **F8** 鎖定目標 → 2. 新增要監看的記憶體位址（或匯入 JSON）→ 3. 勾選要記錄的項目
→ 4. 選「組別」（例如 A），可先填備註 → 5. 遊戲中按 **F9** 開始，需要時按 **F10** 加標記
→ 6. 再按 **F9** 停止，立刻匯出、不跳視窗 → 7. 得到 `session_<時間>_A-01_<程式>.zip`（預設在「文件\GameRec\sessions」），備註可事後補寫再按「更新上一段的備註」

位址欄位格式：

| 寫法 | 意思 |
|---|---|
| `0x1A2B3C40` | 絕對位址 |
| `game.exe+0x2233F0` | 模組基址 + 偏移 |
| `game.exe+0x1F00A0,0x10,0x48` | 指標鏈 `[[base+0x1F00A0]+0x10]+0x48` |

座標等位址可以先用 Cheat Engine 找；內建的 `gamerec.memory.scanner.ValueScanner` 提供首次/再次掃描，介面會在 M2 加上。

## 開發者

每次推到 `main`，GitHub Actions 會在 Windows 上跑測試並建置 `GameRec.exe`；
推 `v*` 標籤（例如 `v0.1.0`）會自動發佈到 Releases。

本機從原始碼執行 / 自行打包：

```bat
pip install -r requirements-dev.txt
python -m gamerec               :: 開 GUI
python -m gamerec record --help :: 命令列模式（給日後自動化用）
scripts\build_exe.bat            :: 產出 dist\GameRec.exe
```

```bash
pip install pytest
pytest          # 核心邏輯（時鐘、session、記憶體解析、匯出）在任何平台都可測
```
