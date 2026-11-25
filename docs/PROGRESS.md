# 專案工作進度

- 專案：Ai 遊戲逆向（兩個獨立程式）
- 最後更新：2026-11-25
- Repo：https://github.com/Addiorsoe/gamerec

| 程式 | 用途 | 狀態 |
|---|---|---|
| **程式一：GameRec 監控軟體** | 監控指定程式的記憶體、封包、畫面、鍵鼠，匯出成一個 zip 交給 Claude 分析 | 可用版本完成，Windows 自動試跑通過（PR #1） |
| **程式二：AutoPath 自動巡路腳本** | 用 GameRec 錄製資料的分析結果自動跑圖 | 設計完成（`docs/AUTOFARM_DESIGN.md`，PR #2），尚未實作 |

> ⚠️ 線上遊戲多有反外掛，讀記憶體、側錄封包可能被封號，建議用單機遊戲或私服測試。

---

## 需求追蹤

| # | 需求（使用者提出） | 對應程式 | 狀態 | 在哪裡 |
|---|---|---|---|---|
| 1 | 使用 Python | 兩者 | ✅ | 全專案 |
| 2 | 建立新的 repo | — | ✅ | Addiorsoe/gamerec |
| 3 | 監控記憶體、封包、錄影做在同一個程式 | GameRec | ✅ | `gamerec/memory` `net` `video` `inputs` |
| 4 | 一起匯出交給 Claude 分析 | GameRec | ✅ | `session_*.zip`（含 `manifest.json`、`README.txt`） |
| 5 | 分成兩個程式：監控軟體 / 自動巡路腳本 | 兩者 | ✅ 設計 | `docs/AUTOFARM_DESIGN.md` §0 |
| 6 | 完整圖形介面，打包成 exe 直接雙擊執行 | GameRec | ✅ | 單一 `GameRec.exe`、無主控台、自動要求管理員 |
| 7 | 自動檢查執行環境，一鍵安裝 | GameRec | ✅ | 啟動自動檢查；一鍵「以管理員重開」「下載安裝 Npcap」「下載新版」，**已打包在 exe 內**（smoke test 驗證） |
| 8 | 從 GitHub 一鍵下載 / 更新 | GameRec | ✅ | Actions 自動建置；推 `v*` 標籤發佈 Releases；程式內檢查更新 |
| 9 | 試跑找致命 bug | GameRec | ✅ | Windows CI 端到端試跑（見下） |
| 10 | 自訂熱鍵、支援組合鍵 | GameRec ✅ / AutoPath ⏳ | ✅ | 「熱鍵設定…」視窗；`gamerec/hotkeys.py`（AutoPath 共用） |
| 11 | 用滑鼠恢復預設熱鍵 | GameRec ✅ / AutoPath ⏳ | ✅ | 熱鍵設定視窗「恢復預設熱鍵」按鈕；設定檔壞掉自動回預設 |
| 12 | 保留強制停止鍵 | GameRec ✅ / AutoPath ⏳ | ✅ | 固定 **Ctrl+Alt+End**，不可修改、不可被其他功能佔用；立即停止並存檔 |
| 13 | 禁止綁定相同按鍵 | GameRec ✅ / AutoPath ⏳ | ✅ | 重複或與強制停止相同會被拒絕並提示 |
| 14 | 驗證封包、記憶體、輸入資料可判讀 | GameRec | ✅ | 見「資料可判讀性」 |
| 15 | 工作進度以 .md 匯出 | — | ✅ | 本檔 `docs/PROGRESS.md` |
| 17 | 用熱鍵鎖定目前視窗作為目標（不要自動抓） | GameRec | ✅ | 預設 **F8**（可自訂、不可重複）；錄影只錄鎖定的視窗 |
| 18 | 用實際遊戲測記憶體是否合理 | GameRec | ✅ | 《灰燼紀元》（瀏覽器遊戲）實測：掃固定位址不可行，建議改「瀏覽器遊戲模式」直接讀遊戲變數 |
| 16 | 把 Claude 接到電腦 | — | ⏸ 後續 | 依使用者要求延後 |

## GameRec 目前功能

- 選擇目標程式（自動抓主視窗），記憶體監看清單：絕對位址、`模組+偏移`、指標鏈（`game.exe+0x10,0x8` 或 `0x1234,0x10`）
- 同步記錄：記憶體（30Hz，變動才寫）、封包（只留該程式的連線）、視窗錄影（每幀時間戳）、鍵鼠事件、標記
- 熱鍵（可自訂）：鎖定目前視窗 `F8`、開始/停止 `F10`、加標記 `F9`；強制停止 `Ctrl+Alt+End`（固定）
- 環境檢查視窗：Windows 10/11、64 位元、管理員、Npcap、新版本
- 輸出資料夾預設 `文件\GameRec\sessions`

## 試跑結果（GitHub Actions，windows-latest）

每次推送自動執行，用一個「假遊戲」（有視窗、會變動的數值、指標鏈、網路流量）：

| 測試 | 結果 |
|---|---|
| 單元測試 16 項（時鐘、匯出、指標鏈、熱鍵規則、環境檢查、更新檢查、抓包健康檢查） | ✅ |
| 命令列錄製 8 秒 → 驗證 zip | ✅ 記憶體 ~170 筆遞增正確、錄影 ~120 幀可讀、按鍵 14 次都有記錄 |
| 圖形介面自動操作（選程式、加位址、開始、按鈕標記、F9 熱鍵標記、Ctrl+Alt+End 強制停止） | ✅ 兩個標記都有、強制停止立即匯出且不跳視窗 |
| 打包後 `GameRec.exe` 啟動檢查（含環境檢查與一鍵安裝模組） | ✅ 8 秒內開啟視窗、所有模組都在、環境檢查顯示「一鍵下載安裝」 |
| 打包後 exe 錄製同一個假遊戲 | ✅ 記憶體、錄影、按鍵全部正確 |
| 沒裝 Npcap | ✅ 封包標記為 unavailable，其他照常，不會當掉 |

已修正的 bug：**（致命）打包後的 exe 沒有包含程式本身，一開啟就跳「Unhandled exception」**；無主控台 exe 一輸出文字就當掉；封包過濾器失敗時顯示正常卻抓到 0 筆；Windows 上 Ctrl+C 停不下來（PR #2）；背景執行緒直接操作視窗（PR #2）；mss 跨執行緒（PR #2）；錄影結束時在錯誤執行緒釋放截圖資源，導致影片明明正常卻標示錯誤。

## 資料可判讀性

匯出 zip 內每個檔案都附在 `README.txt` 說明；全部用同一個 `t_ns`（距開始的奈秒）對齊。

| 檔案 | 格式 | 範例 |
|---|---|---|
| `memory/samples.csv` | `t_ns,name,value` | `523400100,player_x,12.5` |
| `network/packets.jsonl` | 每行一個 JSON | `{"t_ns":710316312,"dir":"out","proto":"udp","src":"127.0.0.1","sport":57703,"dst":"127.0.0.1","dport":47000,"len":6,"payload_hex":"706f73203235","payload_ascii":"pos 25"}` |
| `input/events.jsonl` | 每行一個 JSON | `{"t_ns":2400000000,"kind":"key_down","key":"w"}` |
| `markers.jsonl` | 每行一個 JSON | `{"t_ns":2423013100,"label":"walk north"}` |
| `video/frames.csv` | `frame,t_ns,left,top,width,height` | 第幾幀對應哪個時間 |

CI 每次都會解析全部 JSONL / CSV 並印出樣本；封包內容在 Linux 實測（Windows CI 沒有 Npcap）。

## 尚未驗證 / 已知限制

- Npcap 實際抓封包只在 Linux 驗證過；Windows 上需要你裝好 Npcap 後實機試一次
- Npcap 免費版沒有靜默安裝，一鍵安裝會開啟官方安裝精靈，需手動按下一步（記得勾 WinPcap 相容模式）
- 真實遊戲可能有反外掛，讀不到記憶體或被偵測

## 下一步

0. 決定是否加入「瀏覽器遊戲模式」（直接讀 JS 遊戲變數，給《灰燼紀元》這類遊戲用）
1. 你合併 PR #1、#2，我發佈第一個 Release（`v0.1.0`），之後可直接下載 exe
2. 你在 Windows 上用單機遊戲錄一段，把 zip 給我分析
3. 依分析結果實作 AutoPath（沿用同一套熱鍵模組，包含固定強制停止鍵）
