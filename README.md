# GameRec

指定一個 Windows 程式，**同時**記錄它的：

- 🧠 **記憶體**：監看指定位址（絕對位址 / 模組+偏移 / 指標鏈），30Hz 取樣
- 🌐 **封包**：只保留該程式的 TCP/UDP 連線（pcap + JSONL）
- 🎬 **畫面**：錄下遊戲視窗，每幀都有時間戳
- ⌨️ **鍵鼠輸入**：玩家按了什麼（巡路分析用）
- 🚩 **標記**：錄影中按 F9 加註「現在開始往北走」之類的標籤

全部用同一條時間軸，停止後匯出成一個 `session_*.zip`，直接交給 Claude 分析。
詳細設計與匯出格式見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

> ⚠️ 讀取其他程式記憶體與側錄封包，在多數線上遊戲會被反外掛偵測並違反服務條款，可能導致封號。
> 請在單機遊戲、自架私服或測試帳號上使用。

## 安裝（Windows 10/11）

1. 安裝 Python 3.11+（64 位元）
2. 安裝 [Npcap](https://npcap.com/)，勾選「WinPcap API-compatible Mode」（抓封包需要）
3. 在專案資料夾：
   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. **用系統管理員身分**開啟終端機（讀記憶體與抓封包都需要）

## 使用

```bat
:: 圖形介面
python -m gamerec

:: 列出程式
python -m gamerec ps

:: 命令列錄 60 秒
python -m gamerec record --process game.exe --watch examples_watchlist.json --seconds 60 --notes "從城鎮走到傳送點"
```

GUI 流程：選程式 → 新增監看位址（或匯入 JSON）→ 開始 → 遊戲中按 F9 加標記 → 停止並匯出。

位址欄位格式：

| 寫法 | 意思 |
|---|---|
| `0x1A2B3C40` | 絕對位址 |
| `game.exe+0x2233F0` | 模組基址 + 偏移 |
| `game.exe+0x1F00A0,0x10,0x48` | 指標鏈 `[[base+0x1F00A0]+0x10]+0x48` |

座標等位址可以先用 Cheat Engine 找；內建的 `gamerec.memory.scanner.ValueScanner` 提供首次/再次掃描，介面會在 M2 加上。

## 打包成 exe

```bat
pip install -r requirements-dev.txt
scripts\build_exe.bat
```

產出 `dist\GameRec\GameRec.exe`（會要求系統管理員權限）。

## 開發

```bash
pip install pytest
pytest          # 核心邏輯（時鐘、session、記憶體解析、匯出）在任何平台都可測
```
