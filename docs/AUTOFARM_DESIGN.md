# 自動巡路／刷圖完整設計（兩個獨立程式）

> 本文件延續 `docs/ARCHITECTURE.md`，說明從「採集資料」到「自動跑圖刷圖可實際使用」的完整流程、需求、資料標記規格、分析管線、巡路腳本架構，以及**隨遊戲更新維護**的方法。
>
> ⚠️ 對線上遊戲使用自動化腳本通常違反服務條款，可能封號。本設計以單機、自架私服或測試帳號為前提；**不包含**任何繞過或隱藏於反外掛偵測的設計。
>
> 📌 **判讀原則（使用者指定）**：兩個程式都**不自行判斷「哪個操作對應哪個封包」**。程式只負責忠實記錄原始資料與使用者自己打的標記；操作與封包、記憶體之間的對應關係，由使用者把匯出檔交給 Claude 判讀，確認後才寫進設定檔。唯一例外是**可以保證準確**的對應（例如使用者自己在 `actions.yaml` 寫明「按鍵 1 = 攻擊」，程式只是照表轉名稱，沒有任何推測）。

---

## 0. 兩個程式的分工

本專案是**兩個獨立程式**，各自打包成自己的 exe，可以分開安裝、分開更新。兩者之間只透過「檔案」溝通，不互相 import 對方的程式碼：

| | **程式一：GameRec 監控軟體** | **程式二：AutoPath 自動巡路腳本** |
|---|---|---|
| 用途 | 指定一個程式，監測並錄下它的記憶體位址、封包、畫面、鍵鼠輸入與標記 | 讀 GameRec 的錄製結果做分析，建立地圖與遊戲設定檔，然後自動巡路／刷圖 |
| 對遊戲的動作 | **只讀**（讀記憶體、被動抓封包、截圖） | 只讀記憶體＋送出一般鍵鼠輸入 |
| 輸入 | 目標程式、監看清單（可直接使用 profile 的 `memory.yaml`） | GameRec 匯出的 `session_*.zip`、`profiles/<game>/` |
| 輸出 | `session_*.zip`（manifest + 各串流 + 標記） | `profiles/<game>/`（地圖、opcode 表、路線）、`bot_log.jsonl`、分析報告 |
| exe | `GameRec.exe` | `AutoPath.exe` |
| 程式碼 | `gamerec/`（現有） | `autopath/`（新增） |

```
 ┌────────────── 程式一：GameRec ──────────────┐            ┌──────────── 程式二：AutoPath ────────────┐
 │ 監測記憶體 / 封包 / 錄影 / 輸入 / 標記       │  session   │ 檢視模式：對齊時間軸、整理原始資料、建地圖   │
 │ 版本指紋、自動動作標記、事後標記             │ ──zip────► │ 設定檔工具：AOB 重掃 / 產生簽章 / 回歸測試   │
 └─────────────────────▲───────────────────────┘            │ 巡路模式：感知 → 狀態機 → A* → 鍵鼠輸入     │
                       │  profiles/<game>/memory.yaml        └───────────────┬──────────────────────────┘
                       └────────（監看清單共用同一份位址定義）◄──────────────┘
                     AutoPath 執行時也可以呼叫 GameRec CLI 同步錄影，當作除錯與回歸資料
```

**共用的只有規格與一個小型函式庫** `gamecommon/`：session 格式讀寫、記憶體型別解碼、AOB 簽章解析、profile 讀取。兩個程式都依賴它，但彼此獨立。這樣的好處：

- GameRec 可以單獨用在任何遊戲或程式的觀察，不受巡路功能影響。
- AutoPath 更新（新地圖、新戰鬥邏輯）不需要重新發佈 GameRec，反之亦然。
- 規格（`format_version`、profile schema）是兩者之間的合約，改版時兩邊都要相容舊版本。

### 0.1 需求追蹤（所有要求都對應到哪裡）

| 使用者要求 | 負責的程式 | 本文件章節 |
|---|---|---|
| 指定監測某個程式的記憶體與封包 | GameRec | ARCHITECTURE.md §3、本文件 §3 |
| 錄影並記錄分析指定的記憶體位址與封包效果 | GameRec（錄製）＋ AutoPath（分析） | ARCHITECTURE.md §2、§4；本文件 §4 |
| 用以上分析結果製作自動巡路 | AutoPath | §5、§6.3 |
| 自動跑圖刷圖 | AutoPath | §6（狀態機、戰鬥、撿物） |
| 盡可能採集封包與動作的關聯，並打上標記方便寫腳本 | GameRec（記錄＋使用者標記）；對應關係由 Claude 判讀匯出檔 | §3、§4 |
| 程式不自行判斷操作與封包的對應（除非保證準確） | 兩者 | 文件開頭判讀原則、§3.2、§4 |
| 從開始到投入使用的完整流程與需求 | 兩者 | §1、§2 |
| 隨遊戲更新一併更新或方便維護 | 兩者（共用 profile） | §5、§7 |
| 自訂熱鍵（組合鍵、滑鼠恢復預設、保留強制停止鍵、禁止重複綁定） | 兩者（AutoPath 規格見本文件；GameRec 依同一份規格） | §6.6、F12、N8 |
| 刷圖流程：互動點 → 選地圖 → 清稀有怪 → 打頭目 → 離開；躲避危險地面與招式、技能閃避 | AutoPath | §6.7 |

## 0.2 總覽

```
 ┌──── 階段 A：採集（GameRec）────┐   ┌─ 階段 B：判讀（Claude）──┐   ┌ 階段 C：自動化（AutoPath）┐
 GameRec 錄製（記憶體/封包/影片/輸入）    autopath view + Claude 判讀 autopath run
   + 使用者標記（熱鍵 + 事後補標） ──►   對齊時間軸 → 原始資料整理 ──►  感知 → 狀態機 → 行動
   + 遊戲版本指紋                       → 交給 Claude 判讀 → 確認後     → A* 尋路 → 戰鬥/撿物迴圈
                                          寫入 profile → 地圖建置
                                        → 產出 game profile          → 安全停止 / 紀錄
                    ▲                                                          │
                    └──────── 階段 D：維護（遊戲更新 → 簽章重掃 → 回歸驗證）◄─────┘
```

核心理念：**所有遊戲相關知識都放在「遊戲設定檔（game profile）」而不是程式碼裡**。遊戲更新時，只更新設定檔，並用回歸錄影自動驗證。

---

## 1. 階段與里程碑（從開始到投入使用）

| 階段 | 名稱 | 產出 | 驗收條件 |
|---|---|---|---|
| **P0** | 目標確認 | `profiles/<game>/README.md`：遊戲名稱、版本、平台、是否有反外掛、用途（單機/私服/測試帳號） | 確認可合法使用；記錄目前版本號 |
| **P1** | 關鍵位址探索 | `profiles/<game>/memory.yaml` 初版：座標 x/y/z、地圖 ID、HP/MP、目標 ID、背包數量 | 每個欄位都有 **AOB 簽章** 或指標鏈，重開遊戲 3 次仍正確 |
| **P2** | 標記式錄製 | 10～30 段已標記的 session（走路、攻擊、施法、撿物、換圖、死亡、回城） | 每類動作至少 20 次樣本；manifest 含版本指紋 |
| **P3** | 封包／記憶體判讀 | 使用者把匯出 zip 交給 Claude 判讀 → 使用者確認後寫入 `profiles/<game>/packets.yaml`（每筆標 `status: confirmed`） | 主要動作（移動、攻擊、撿物）的對應都經使用者確認；未確認的項目 AutoPath 不使用 |
| **P4** | 地圖建置 | `profiles/<game>/maps/<map_id>.json`：可走格網、傳送點、刷怪區、路點 | 在該地圖由錄製軌跡覆蓋 ≥ 80% 可走區 |
| **P5** | Bot 原型（只觀察） | `autopath run --dry-run`：只讀狀態、規劃路徑、顯示「打算做什麼」，不送出輸入 | 狀態判讀與畫面一致率 ≥ 95% |
| **P6** | Bot 原型（閉環） | 用**模擬鍵鼠輸入**走路、打怪、撿物 | 指定地圖連續跑 30 分鐘無卡死，卡住能自動脫困 |
| **P7** | 穩定化 | 例外處理、死亡/背包滿/斷線處理、看門狗、完整紀錄 | 連續 4 小時無人工介入；所有停止原因皆有紀錄 |
| **P8** | 投入使用＋維護 | 版本偵測、簽章重掃工具、回歸測試、更新 SOP | 遊戲更新後依 SOP 在 1 小時內恢復 |

> 建議順序：P1→P2→P3 可以反覆迭代；P5 之前不送出任何輸入，降低誤操作風險。

---

## 2. 需求

### 2.1 功能需求

| 編號 | 需求 |
|---|---|
| F1 | GameRec 支援使用者標記（熱鍵、按鈕、事後補標），記錄開始與結束時間；按鍵只在使用者自訂的對照表內照表轉成動作名稱，不做推測 |
| F2 | 每次錄製自動寫入遊戲版本指紋（exe 檔案版本、SHA-256、主要模組大小與時間戳） |
| F3 | AutoPath 能把多個串流對齊到同一時間軸並匯出成易讀格式（例如「每個標記前後 1 秒內的封包與記憶體變化」），只整理、不下結論，方便交給 Claude 判讀 |
| F4 | 封包與記憶體的對應只能來自使用者確認過的 `packets.yaml` / `memory.yaml`；程式不自動推斷欄位意義 |
| F5 | 由座標軌跡建立可走格網地圖與路點圖，支援手動編修 |
| F6 | Bot 有狀態機（巡邏/尋怪/戰鬥/撿物/補給/回城/脫困/停止） |
| F7 | Bot 以 A* 在格網上尋路，並以路點追蹤做閉環移動控制 |
| F8 | Bot 所有行為都可在 `--dry-run` 下只顯示不執行 |
| F9 | 所有遊戲相關位址、封包格式、按鍵配置都在 profile 設定檔中，程式碼不含寫死偏移 |
| F10 | 位址以 AOB 簽章為主、指標鏈為輔，啟動時自動解析並驗證 |
| F11 | Bot 執行過程可同時用 GameRec 錄製，作為除錯與回歸資料 |
| F12 | AutoPath 所有熱鍵可自訂，支援組合鍵（如 `Ctrl+Shift+P`）；禁止兩個功能綁同一組按鍵；可用滑鼠一鍵恢復預設；強制停止鍵永遠存在、不能被清除（見 §6.6） |

### 2.2 非功能需求

| 編號 | 需求 |
|---|---|
| N1 | 安全停止：全域熱鍵（預設 `F12`）立即停止並放開所有按鍵；看門狗偵測狀態讀不到超過 N 秒就停止 |
| N2 | 預設只讀：Bot 不寫入遊戲記憶體、不注入、不修改或偽造封包；行動只透過一般鍵鼠輸入 |
| N3 | 可追蹤：每個決策（狀態轉換、目標選擇、路徑）寫入 `bot_log.jsonl`，與 GameRec 同一時間軸 |
| N4 | 可測試：感知、尋路、狀態機都能用錄製檔離線重播測試，不需開遊戲 |
| N5 | 效能：決策迴圈 ≥ 10Hz；A* 在 512×512 格網內 < 50ms |
| N6 | 版本不符時拒絕啟動 Bot（只允許錄製與分析），避免讀到錯誤位址亂操作 |
| N7 | 資料不外傳：分析與 Bot 皆本機執行；錄製 zip 只在使用者自行上傳時離開電腦 |
| N8 | 熱鍵設定錯誤不能讓程式失控：設定檔損毀或不合法時自動退回預設值並提示；Bot 自己送出的按鍵不會觸發 AutoPath 自己的熱鍵 |

---

## 3. GameRec 的標記規格擴充（讓後續腳本好寫）

現行 `markers.jsonl` 只有 `{t_ns, label}`。擴充為**結構化事件標記**，`format_version` 升為 2（向下相容：舊檔的 label 視為 `kind="note"`）。

### 3.1 `annotations.jsonl`（新增，取代 markers 的主要用途）

```json
{"id": 12, "t_start_ns": 15200000000, "t_end_ns": 15900000000,
 "kind": "action", "action": "attack", "source": "keymap",
 "args": {"key": "1", "skill": "普攻"}, "target": {"mob_id": 3021},
 "tags": ["map:101", "combat"], "note": ""}
```

| 欄位 | 說明 |
|---|---|
| `id` | session 內唯一，方便互相參照 |
| `t_start_ns` / `t_end_ns` | 動作區間；瞬間事件兩者相同 |
| `kind` | `action`（玩家動作）、`event`（遊戲事件，如怪死亡、升級、換圖）、`state`（持續狀態，如戰鬥中）、`note`（自由備註） |
| `action` | 受控詞彙，見 3.2 |
| `source` | `hotkey`（玩家按標記熱鍵）、`manual`（事後在時間軸上補標）、`keymap`（依使用者自訂的按鍵對照表照表轉換，非推測） |
| `args` | 動作參數：按鍵、技能名、點擊座標等 |
| `target` | 目標：怪物、NPC、物品、地圖座標 |
| `tags` | 自由標籤，建議 `map:<id>`、`area:<名稱>` |

### 3.2 按鍵對照表（`profiles/<game>/actions.yaml`，由使用者填寫）

```yaml
actions:
  move:      {keys: [w, a, s, d], hold: true}         # 鍵盤移動
  move_click:{mouse: left, on: ground}                 # 點地移動
  attack:    {keys: ["1"]}
  skill:     {keys: ["2", "3", "4", "5"]}
  pickup:    {keys: [z]}
  use_item:  {keys: [f1, f2]}                          # 喝水
  interact:  {keys: [space]}                           # NPC / 傳送門
  teleport:  {keys: [t]}                               # 回城卷
events:      [mob_killed, item_dropped, item_looted, map_changed, player_died, level_up, inventory_full]
```

這張表是**使用者自己寫的事實**（「我把攻擊設在 1」），所以照表把 `input/events.jsonl` 的按鍵轉成動作名稱是準確的，標為 `source: keymap`；表裡沒有的按鍵就保持原始按鍵，不猜。

程式**不會**做的事：
- 不判斷某個動作送出了哪個封包、某個封包代表什麼。
- 不從記憶體變化推測遊戲事件（例如不會因為經驗值上升就自動標「怪死亡」）；遊戲事件由使用者用熱鍵或事後補標。

這些對應交給 Claude 判讀匯出檔，使用者確認後才寫進 `packets.yaml`。

### 3.3 GUI／熱鍵擴充

| 熱鍵 | 功能 |
|---|---|
| `F9` | 一般標記（保留現行行為） |
| `F5`～`F8` | 可在設定中綁定常用事件（預設：怪死亡、掉落、撿到、換圖） |
| `Ctrl+F9` | 開始／結束一個「區段」，例如「這段是在刷 A 區」 |

另外新增「事後標記」視窗：拖動影片時間軸、看到同一時刻的記憶體與封包原始值，點選補標或修改標記。

### 3.4 版本指紋（寫入 `manifest.json`）

```json
"game_fingerprint": {
  "exe_file_version": "1.2.345.0",
  "exe_sha256": "ab12...",
  "modules": [{"name": "game.exe", "size": 48234496, "timestamp": 1764000000}],
  "profile": {"name": "mygame", "version": "2026.11.20", "signatures_ok": 9, "signatures_failed": 0}
}
```

### 3.5 錄製建議（讓 Claude 判讀更準確）

- **刻意的單一動作錄製**：每段錄製只做一種動作（例如原地重複攻擊同一隻怪 20 次），中間停頓 1 秒以上，對應關係會清楚很多。
- **控制變因**：走路時分別錄「只往東」、「只往北」，有助辨認座標欄位與方向欄位。
- **閒置基準**：每段開頭先閒置 10 秒，讓判讀時能區分心跳/背景封包。
- **記憶體加錄**：除了座標，也監看目標 ID、背包數量、經驗值。程式只記錄數值，但判讀時這些變化是很有用的佐證。
- **事件當下按熱鍵**：怪死了、撿到東西時順手按 F5～F8，標記越多，判讀越準。

---

## 4. 判讀流程（程式整理資料，Claude 判讀，使用者確認）

```
 GameRec 匯出 zip ─► AutoPath 檢視模式（只整理，不下結論）─► 使用者把檔案貼給 Claude
                                                              │
   AutoPath 只使用 confirmed 項目 ◄─ 寫入 profile ◄─ 使用者確認 ◄─ Claude 判讀報告
```

| 步驟 | 誰做 | 內容 | 輸出 |
|---|---|---|---|
| 1. 對齊時間軸 | AutoPath（`autopath view`） | 所有串流依 `t_ns` 合併；影片幀用 `frames.csv` 對應 | `timeline.csv`（一行一事件，依時間排序） |
| 2. 整理原始資料 | AutoPath | 只做無判斷的整理：每個標記前後 N 秒內的輸入、封包、記憶體變化列表；依方向與長度列出封包；**不計算關聯、不推斷欄位** | `around_markers.md`、`packets_by_len.csv` |
| 3. 判讀 | Claude | 使用者把 zip（或第 2 步的檔案）貼來，Claude 判讀哪些封包、記憶體變化對應哪個操作，並標出不確定的地方與建議補錄的動作 | 判讀報告（含信心說明） |
| 4. 確認 | 使用者 | 檢查判讀結果，必要時補錄驗證 | — |
| 5. 寫入設定檔 | 使用者／Claude | 確認的項目寫入 `packets.yaml`、`memory.yaml`，每筆標 `status: confirmed` 和確認日期 | profile |
| 6. 地圖建置 | AutoPath | 依地圖 ID 分組座標軌跡（座標是使用者確認過的記憶體位址，屬直接讀值）→ 格網化 → 走過的格子為可走；換圖點、刷怪區由使用者標記決定 | `maps/<id>.json` |

> AutoPath 在巡路時**只使用 `status: confirmed` 的項目**。未確認或缺少的項目一律不用，寧可功能少一點也不冒錯誤操作的風險。
> 封包若加密或壓縮看不懂也沒關係：巡路只靠記憶體讀值與鍵鼠輸入，封包只是判讀時的參考資料。

---

## 5. 遊戲設定檔（game profile）結構

```
profiles/<game>/
  profile.yaml        # 名稱、支援版本清單、視窗標題、時間設定
  memory.yaml         # 位址定義：AOB 簽章 + 指標鏈 + 型別 + 驗證規則
  packets.yaml        # opcode 與欄位定義（Claude 判讀 + 使用者確認，每筆 status: confirmed）
  actions.yaml        # 動作 ↔ 按鍵對照
  maps/<map_id>.json  # 格網、路點、傳送點、刷怪區
  routes/<name>.yaml  # 刷圖路線（循環路點、目標怪清單、停留條件）
  regression/         # 回歸用的小段錄製 + 預期值
  CHANGELOG.md        # 每次遊戲更新後修改了什麼
```

### 5.1 `memory.yaml`（AOB 優先）

```yaml
game_versions: ["1.2.345.0", "1.2.346.0"]
fields:
  player_base:
    signature: "48 8B 0D ?? ?? ?? ?? 48 85 C9 74 ?? F3 0F 10 41 ??"
    module: game.exe
    resolve: rip_relative        # 取 [指令位址 + 7 + disp32]
    disp_offset: 3
    deref: true
  player_x:  {base: player_base, offsets: [0x48], type: float}
  player_y:  {base: player_base, offsets: [0x4C], type: float}
  map_id:    {base: player_base, offsets: [0x120], type: int32}
  hp:        {base: player_base, offsets: [0x200], type: int32}
  # 簽章找不到時的備援
  fallback:
    player_base: {module: game.exe, offsets: [0x1F00A0]}
validate:
  - {field: hp,     range: [0, 999999]}
  - {field: map_id, range: [1, 9999]}
  - {field: player_x, finite: true}
```

- `?? ` 為萬用位元組，避開每次編譯會變的絕對位址與偏移。
- 結構內偏移（`0x48` 等）較少隨更新變動；若變動，可由簽章附近的指令（例如 `F3 0F 10 41 ??` 中的 `??`）自動擷取（`extract: {at: 16, size: 1}`）。
- `validate` 讓載入時立即發現位址錯誤，不符合時拒絕啟動 Bot（N6）。

### 5.2 `routes/<name>.yaml`

```yaml
map_id: 101
loop: [wp_03, wp_07, wp_12, wp_07]   # 路點 ID（定義於 maps/101.json）
targets: {mob_ids: [3021, 3022], max_distance: 15}
loot: {enabled: true, radius: 6}
supply: {hp_below: 0.35, use: use_item:f1, return_town_when: [inventory_full, no_potion]}
stop_after: {minutes: 120}
```

---

## 6. 巡路腳本架構（AutoPath 巡路模式：`autopath run`，模組 `autopath/bot/`）

```
            ┌──────────────────── Bot 主迴圈（10～20Hz）────────────────────┐
            │                                                               │
 Perception │  MemoryReader（profile 解析位址）→ WorldState                  │
 (感知)      │  （可選）PacketObserver：確認動作被送出/接受                   │
            │  （可選）ScreenProbe：讀畫面像素做備援判斷（例如對話框）        │
            ▼                                                               │
 Decision   │  StateMachine ──► 當前狀態的 Behavior                          │
 (決策)      │  Navigator（A* + 路點追蹤）  TargetSelector  LootSelector     │
            ▼                                                               │
 Action     │  InputDriver（SendInput 模擬鍵鼠；--dry-run 只記錄）           │
 (行動)      │  SafetyGuard：F12 停止、看門狗、速率限制、版本鎖               │
            ▼                                                               │
 Logging    │  bot_log.jsonl（決策/轉換/路徑）＋可呼叫 GameRec CLI 同步錄製  │
            └───────────────────────────────────────────────────────────────┘
```

### 6.1 WorldState

```python
@dataclass
class WorldState:
    t_ns: int
    pos: Vec2; map_id: int
    hp: float; hp_max: float; mp: float
    target_id: int | None
    nearby_mobs: list[Entity]      # 若找得到實體清單
    nearby_items: list[Entity]
    inventory_free: int
    is_dead: bool; in_combat: bool
```

### 6.2 狀態機

```
            ┌─────────┐ 找到怪 ┌─────────┐ 怪死亡且有掉落 ┌────────┐
 START ───► │ PATROL  │──────►│ COMBAT  │──────────────►│  LOOT  │
            └────┬────┘       └────┬────┘               └───┬────┘
                 ▲   沒有怪/撿完    │ HP 低                  │
                 └─────────────────┼────────────────────────┘
                                   ▼
                              ┌─────────┐ 補完
                              │ RECOVER │──────► PATROL
                              └────┬────┘
              背包滿/沒藥          │
          ┌────────────────────────┘
          ▼
     ┌──────────┐ 回到刷圖點   任何狀態：位置 N 秒不動且有移動意圖 → UNSTUCK
     │ RETURN   │───────────► PATROL     死亡 → DEAD（停止或依設定復活）
     └──────────┘                        F12 / 看門狗 / 版本不符 → STOP
```

- 每個狀態實作 `enter()`, `tick(state) -> Transition | None`, `exit()`。
- 轉換條件全部寫在設定（例如 `hp_below: 0.35`），方便不改程式就能調整。
- 每次轉換寫入 `bot_log.jsonl`：`{t_ns, from, to, reason, snapshot}`。

### 6.3 尋路與移動

1. **全域**：路點圖（waypoint graph）上用 A* 找路點序列；跨地圖時用傳送點連接不同地圖的圖。
2. **區域**：在格網上 A*（8 方向，對角成本 √2，啟發函數為 octile 距離），平滑化（line-of-sight 剪枝）。
3. **追蹤**：每個 tick 計算目前位置到下一個子目標的方向，轉成按鍵（WASD 組合）或點地座標（需要「遊戲座標 → 螢幕座標」換算，從錄製中以點擊位置與之後的目的地座標迴歸求得）。
4. **到達判定**：距離 < 容許值即前進到下一點；
5. **脫困**：位置在 2 秒內移動 < 閾值 → 依序嘗試：後退＋側移、跳躍、重新規劃（把該格標為暫時不可走）、回到上一個路點；3 次失敗 → 停止並記錄。
6. **地圖學習**：Bot 執行中走過的新格子回寫為可走、卡住的格子回寫為障礙（存成 overlay，不直接改原圖，可人工審核後合併）。

### 6.4 戰鬥／撿物迴圈

```
COMBAT.tick:
  if 沒有目標 or 目標死亡: 選最近且在路線範圍內的怪 → 走到攻擊距離
  依技能輪替表（冷卻時間由設定或由封包/記憶體觀察）送出按鍵
  若 N 秒目標 HP 不變 → 換目標或判定為打不到（加入短期黑名單）
LOOT.tick:
  依掉落物清單（或記憶體實體清單）由近到遠走過去按撿取
  以背包數量變化（記憶體）或撿物封包確認成功；失敗重試 2 次後放棄
```

### 6.5 安全機制

- `F12` 全域停止，並對所有按住的鍵送出 key-up。
- 看門狗：WorldState 讀取失敗、時間戳停止前進、視窗失去焦點 → 暫停。
- 速率上限：每秒輸入事件數上限，避免邏輯錯誤時狂按。
- 版本鎖：啟動時比對 `game_fingerprint` 與 `profile.yaml` 的支援版本清單、執行 `validate`，不符則拒絕。

### 6.6 自訂熱鍵

**預設熱鍵**

| 功能 | 預設 | 可改 | 可清除 |
|---|---|---|---|
| 強制停止（停止 Bot 並放開所有按鍵） | `F12` | 可 | **不可**（永遠必須有一組） |
| 備用強制停止 | `Ctrl+Alt+End` | **不可**（寫死，最後防線） | 不可 |
| 開始／暫停 | `F10` | 可 | 可 |
| 切換 dry-run | `Ctrl+F10` | 可 | 可 |
| 標記目前位置為路點（建地圖用） | `Ctrl+F11` | 可 | 可 |
| 標記目前位置為障礙 | `Shift+F11` | 可 | 可 |

**組合鍵規則**
- 格式：0～3 個修飾鍵（`Ctrl`、`Alt`、`Shift`、`Win`）＋ 1 個主鍵，例如 `Ctrl+Shift+P`。左右 Ctrl/Alt/Shift 視為同一個。
- 只按修飾鍵（例如只有 `Ctrl`）不算合法熱鍵。
- 觸發時機：主鍵按下且修飾鍵剛好是指定的那組（`Ctrl+P` 不會被 `Ctrl+Shift+P` 觸發）。
- 設定方式：在熱鍵設定視窗點「錄製」按鈕後直接按下想要的組合，不需要手打文字。

**驗證規則（儲存前檢查，任何一條不過就拒絕儲存並標紅）**
1. 不可重複：兩個功能不可綁同一組（正規化後比較，`LCtrl+P` 等於 `RCtrl+P`）。
2. 不可與備用強制停止 `Ctrl+Alt+End` 相同。
3. 不可與 profile `actions.yaml` 裡 Bot 要送出的遊戲按鍵衝突（例如攻擊鍵 `1`），否則 Bot 自己按鍵時會觸發熱鍵；含修飾鍵的組合只要遊戲按鍵本身沒有用到同一組就可以。
4. 不可使用系統保留組合：`Ctrl+Alt+Del`、`Alt+F4`、`Alt+Tab`、`Win+L`、`Win+D` 等。
5. 強制停止欄位不可為空。

**避免熱鍵造成致命問題的設計**
- **滑鼠恢復預設**：設定視窗有「恢復預設熱鍵」按鈕，主視窗工具列也有一個，全程用滑鼠就能完成，不需要按任何鍵（就算鍵盤熱鍵全部設壞也能救回）。
- 另外保留兩個不靠介面的救援方式：`AutoPath.exe --reset-hotkeys`，或刪除 `hotkeys.json`。
- 設定檔 `hotkeys.json` 有 `schema_version`；讀取失敗或驗證不過 → 自動用預設值並在狀態列提示，絕不帶著不合法設定啟動。
- Bot 送出的按鍵帶有 Windows「注入」標記（`LLKHF_INJECTED`），熱鍵監聽會忽略這類事件，只回應真人按鍵。
- 強制停止的處理在獨立的高優先執行緒，不經過 GUI 事件迴圈，所以即使介面卡住也能停。停止時一律對所有按下中的鍵送出 key-up。
- 主視窗永遠有一顆「■ 停止」按鈕，可用滑鼠停止。

**設定檔範例（`%APPDATA%/AutoPath/hotkeys.json`）**

```json
{
  "schema_version": 1,
  "bindings": {
    "force_stop": "F12",
    "start_pause": "F10",
    "toggle_dry_run": "Ctrl+F10",
    "mark_waypoint": "Ctrl+F11",
    "mark_obstacle": "Shift+F11"
  }
}
```

> 這套規則（組合鍵格式、驗證、恢復預設、強制停止）建議放在共用函式庫 `gamecommon/hotkeys.py`，讓 GameRec 也用同一份實作與測試，兩個程式行為一致。

### 6.7 目標玩法：地圖刷怪流程（使用者需求統整）

**流程**：前往特定互動點 → 互動 → 開啟地圖選擇介面 → 選擇地圖並開始 → 進入地圖後以**稀有怪**為目標全部擊殺 → 前往**頭目**擊殺 → 離開地圖 →（循環）。全程要**躲避危險地面與招式**，可以用**技能閃躲**。

#### 6.7.1 需求明細

| 編號 | 需求 | 驗收條件 |
|---|---|---|
| R1 | 從目前位置自動走到指定互動點（NPC／地圖裝置） | 互動點座標寫在 profile，抵達距離 < 容許值，卡住會脫困 |
| R2 | 與互動點互動並開啟地圖選擇介面 | 互動後在 N 秒內確認介面已開啟（依已確認的記憶體旗標或畫面範本），失敗重試 2 次後停止 |
| R3 | 在介面中選擇指定地圖並開始 | 地圖清單、點選位置或按鍵順序寫在 profile；確認已進入地圖（地圖 ID 改變）才往下走 |
| R4 | 地圖內搜尋並擊殺**所有稀有怪** | 稀有怪的判斷依據（記憶體中的怪物等級欄位，或畫面上的名牌顏色）必須是使用者確認過的；全部擊殺或達到時間上限才進入下一步 |
| R5 | 稀有怪清完後前往**頭目**並擊殺 | 頭目位置或判斷方式寫在 profile；頭目死亡（已確認的判斷依據）才進入下一步 |
| R6 | 離開地圖 | 使用離開方式（傳送門／按鍵／回城）並確認回到城鎮地圖 ID |
| R7 | 循環執行，並可設定次數或時間上限；單張地圖超過 10 分鐘自動離開 | 每輪結果（耗時、擊殺數、失敗原因）寫入 `bot_log.jsonl` |
| R8 | 躲避**危險地面**（持續型地面效果） | 路徑規劃避開危險格；站在危險格上時優先離開 |
| R9 | 躲避**招式**（頭目或怪物的範圍攻擊預兆） | 偵測到預兆後在反應時間內離開範圍 |
| R10 | 可用**技能閃躲**（翻滾、衝刺、瞬移等） | 閃避技能、距離、冷卻寫在 profile；冷卻中改用走的 |
| R11 | 任何一步失敗都有明確的停止或回復策略 | 例如：介面沒打開就重試，死亡就停止或依設定復活，超時就離開地圖 |
| R12 | 每輪開始前確認進圖道具（信標）數量足夠，不足就停止並記錄；Bot 不自動購買 | 信標數量的讀取依據經使用者確認 |

#### 6.7.2 流程腳本（`routes/<name>.yaml`，每一步都是可重用的動作）

參考 Koolo「每條路線是一連串 run 步驟」與 Kolbot「戰鬥規則寫在設定檔」的做法：

```yaml
name: map_farm
loop: {max_runs: 50, max_minutes: 120}
map_timeout_min: 10                           # 地圖內超過 10 分鐘直接離開（使用者需求 8）
steps:
  - check_stock:  {item: beacon, min: 1, on_short: stop}                         # R12
  - go_to:        {point: map_device}            # R1，座標定義於 maps/<town>.json
  - interact:     {key: interact, expect: map_ui_open, timeout_s: 3, retry: 2}   # R2
  - select_map:   {region: "區域A", timemark: 8, method: click_template, confirm: map_id_changed} # R3
  - pick_stage:   {by: chaos_card_rarity, prefer: highest}                       # 6.7.10 #2
  - clear:        {targets: rare, until: all_dead, loot: pet_auto}               # R4，寵物自動拾取＋遊戲過濾器
  - kill_boss:    {locate: boss_marker, until: boss_dead, watcher: {enabled: true, difficulty: user}}  # R5、6.7.10 #1
  - leave_map:    {method: portal_key, confirm: town_map_id}                     # R6
on_fail:
  map_ui_open: retry_then_stop
  timeout:     leave_map
  player_died: respawn_then_restart   # 記錄點重生後從第一步重來
  game_crashed: relaunch_then_restart
```

#### 6.7.3 決策：行為樹（取代單純狀態機）

這個玩法有明確的優先順序，用行為樹（參考 `py_trees`）會比單一狀態機好寫好改。每個 tick 從上往下找第一個成立的分支：

```
Selector（由上往下，先成立先做）
├─ 1. 安全：強制停止／看門狗／版本鎖 ………………… 停止
├─ 2. 閃避：自己在危險區內，或偵測到招式預兆 ……… 閃避（技能 or 走）
├─ 3. 補給：HP 低於門檻 ……………………………………… 喝水／後退
├─ 4. 戰鬥：攻擊範圍內有目標（稀有怪 or 頭目）…… 技能輪替
├─ 5. 撿物：附近有符合規則的掉落 …………………… 撿取
└─ 6. 流程：執行 route 的目前步驟 ………………… 走路／互動／選圖／找下一隻稀有怪／找頭目／離開
```

閃避永遠排在戰鬥之前，所以打頭目途中遇到招式會先閃再繼續打。

#### 6.7.4 目標挑選規則（參考 Kolbot 的戰鬥設定）

```yaml
targeting:
  rare:  {priority: 1}                 # 稀有怪優先
  boss:  {priority: 2, only_after: rare_cleared}
  normal: {attack: only_if_blocking}   # 一般怪擋路才打
  skip:  {mob_ids: []}                 # 指定略過的怪
  max_chase_distance: 30
```

尋找稀有怪的順序：先打視野內最近的；視野內沒有就沿著「未探索區域」移動（格網上記錄去過的格子），直到全圖探索完或時間到。

#### 6.7.5 危險地面與招式閃避（影響圖做法）

1. **危險格網**：除了可走格網，再維護一張即時的「危險成本圖」。每個 tick 依目前已知的危險物件（位置、半徑或形狀、剩餘時間）更新。
2. **尋路避開**：A* 的格子成本 = 基本成本 + 危險成本。危險越高越繞，禁止區直接不可走（參考 python-pathfinding 的加權格網）。
3. **逃離**：自己所在格子的危險值超過門檻時，用 BFS 找最近的安全格：
   - 走過去來得及（距離 ÷ 移動速度 < 預估命中時間）就用走的。
   - 來不及，而且閃避技能可用，就朝安全格方向放閃避技能。
   - 都不行就往危險值最低的方向移動。
4. **招式預兆**：每種招式定義在 `profiles/<game>/hazards.yaml`，內容是預兆特徵、範圍形狀（圓、扇形、直線）、延遲時間：

```yaml
hazards:
  fire_ground:  {type: ground, shape: circle, radius: 4, source: memory_effect_list, effect_id: 1203, status: confirmed}
  boss_slam:    {type: telegraph, shape: circle, radius: 6, delay_ms: 900, source: memory_cast_id, cast_id: 55, status: unconfirmed}
dodge:
  skill_key: space
  distance: 5
  cooldown_s: 3
  fallback: walk
```

5. **危險資訊從哪裡來**：遵守「程式不自行判斷」的原則，危險物件的來源必須是使用者確認過的（`status: confirmed`）：
   - **記憶體**：地面效果／技能物件清單、頭目施法 ID（最準、最快）。
   - **畫面**（備援）：危險地面的顏色或範本比對（參考 Botty 的範本比對）。
   - 未確認的招式不會被用來觸發閃避，只會在 log 裡記錄。

#### 6.7.6 要先錄下來交給 Claude 判讀的資料

| 用途 | 錄製內容 | 要找出的東西 |
|---|---|---|
| R2/R3 | 走到互動點 → 互動 → 打開介面 → 選圖 → 進圖，重複 5 次 | 介面開啟旗標、地圖選擇方式、地圖 ID |
| R4 | 在地圖中分別靠近一般怪、稀有怪，各按 F5 標記 | 怪物清單位址、怪物等級（稀有）欄位 |
| R5 | 頭目戰全程，頭目死亡時按 F6 | 頭目判斷方式、頭目 HP／死亡旗標 |
| R8/R9 | 故意站上危險地面、看頭目放招，各按 F7 標記；每次閃避時按 F8 | 地面效果／施法 ID、範圍、延遲時間 |
| R10 | 原地放閃避技能 10 次 | 閃避距離、冷卻時間 |

#### 6.7.8 使用者確認的條件（2026-11-25）與設計對應

| # | 使用者回覆 | 設計決定 |
|---|---|---|
| 1 | 地圖是固定生成，但不想每張新地圖都重新錄製 | 新增**自動探索建圖**（見下方 A），第一次進入新地圖時由 AutoPath 自己探索並存成地圖檔，之後重複使用；不需要人工錄製 |
| 2 | 滑鼠點擊移動；角色在畫面上的位置固定（相對鏡頭），但地形有高度差 | 移動採**方向式點擊**（見下方 B） |
| 3 | 開地圖單純開啟，不額外投入道具（已確認） | `select_map` 只做「選地圖 → 開始」，不操作任何額外投入的道具欄位；介面狀態與預期不符時停止並記錄 |
| 4 | 掉落物全撿，遊戲本身有過濾器 | 撿物不做物品判斷；火炬之光：無限有寵物自動拾取（見 6.7.9），所以預設靠寵物撿，Bot 只在掉落區停留；寵物撿不到的物品類型才用撿取鍵備援 |
| 5 | 閃避技能未定，希望能自己設定按鍵 | 新增**遊戲按鍵設定**（見下方 C），閃避鍵、攻擊鍵等都可自訂，與 AutoPath 熱鍵一起做衝突檢查 |
| 6 | 自行重開；死亡則在記錄點重生 | 遊戲當掉／斷線 → 自動重開遊戲並回到流程起點；死亡 → 等待重生 → 回到流程起點重新開始這一輪（見下方 D） |
| 7 | 執行時還要持續使用電腦 | **影響架構**，見下方 E |
| 8 | 儲存 log；在達成目標下越快越好；超過 10 分鐘自動離開地圖 | 每輪紀錄存 `bot_log.jsonl`，失敗時自動截圖；地圖內總時限 10 分鐘（`map_timeout_min: 10`），超時直接離開；路線順序以最短路徑排序 |

**A. 自動探索建圖（取代每張地圖人工錄製）**
- 進入一張沒有地圖檔的地圖時切到「探索模式」：以 frontier exploration（往已知與未知區域的邊界走）走遍可到達的區域，邊走邊把走得到的格子記為可走、卡住的方向記為障礙。
- 探索同時照常打稀有怪和頭目，所以第一次探索那一輪也算一輪，只是比較慢。
- 地圖檔以地圖 ID 命名存在 `maps/`，之後進入直接用 A* 規劃最短路線；每輪還會補上新走到的區域（overlay）。
- 如果能在記憶體找到遊戲本身的地形碰撞資料（需要錄製後由 Claude 判讀、使用者確認），就直接讀取，第一次也不用探索。
- 互動點、頭目位置可以在探索時由使用者按熱鍵標記一次；若頭目可以從實體清單辨認，則自動記錄位置。

**B. 方向式點擊移動（處理高度差）**
- 因為角色固定在畫面上的同一點，所以不需要精確的「遊戲座標 → 螢幕座標」換算：每個 tick 計算目標方向，在角色螢幕位置往該方向固定半徑（例如 150 px）點擊。
- 高度差只會讓實際移動距離略有誤差，靠閉環修正：每 tick 重新讀座標、重新算方向。
- 需要先錄一次「往 8 個方向點擊移動」，由 Claude 判讀出螢幕方向與遊戲座標軸的對應（鏡頭角度），使用者確認後寫入 profile。

**C. 遊戲按鍵設定（`profiles/<game>/actions.yaml` + GUI）**
- 攻擊技能、閃避技能、撿取、互動、喝水等遊戲按鍵都在 GUI 裡設定，閃避技能另外設定距離與冷卻。
- 與 AutoPath 自己的熱鍵（§6.6）一起檢查：不可重複、不可與強制停止鍵衝突。

**D. 死亡與當機處理**
- 死亡：偵測已確認的死亡旗標 → 等待在記錄點重生 → 記錄失敗原因 → 從流程第一步重新開始。
- 遊戲程式消失或無回應：以 `profile.yaml` 設定的遊戲路徑重開 → 等待進入遊戲 → 從第一步重新開始。
- 已確認重開遊戲後不會再要求帳號密碼，所以可全自動重開。AutoPath 仍**不儲存任何帳號密碼**；萬一出現登入畫面，就停止並記錄。
- 連續失敗 N 次（預設 3）就停止，避免無限重試。

**E. 執行時還要使用電腦（需要使用者選擇）**
一般鍵鼠模擬（SendInput）會搶走真正的滑鼠和鍵盤，而且遊戲視窗必須在最前面，跟「同時用電腦」衝突。可行方案：

| 方案 | 做法 | 優點 | 缺點 |
|---|---|---|---|
| ① 虛擬機（建議先試） | 遊戲＋AutoPath 跑在 Hyper-V／VMware 的 Windows 虛擬機裡，AutoPath 在虛擬機內送輸入 | 完全不干擾主機，主機照常使用 | 需要較好的顯示卡／記憶體；部分遊戲或保護機制不支援在虛擬機執行，要先測試 |
| ② 第二台電腦 | 遊戲與 AutoPath 跑在另一台電腦 | 最穩定 | 需要硬體 |
| ③ 背景視窗訊息 | 用 PostMessage 直接把點擊送到遊戲視窗，不移動真正的滑鼠 | 不需額外環境 | 很多遊戲（DirectInput／Raw Input）不接受，必須實測才知道 |
| ④ 讓位模式 | 偵測到真人動滑鼠或鍵盤就自動暫停，閒置 N 秒後繼續 | 實作簡單 | 不是真正的同時使用 |

建議順序：先實測 ③（成本最低），不行就用 ①；④ 一律內建當作安全機制（真人一動就暫停，避免搶滑鼠造成誤操作）。

> ✅ **使用者已選定（2026-11-25）**：先實測 ③ 背景點擊，遊戲不接受就改用 ① 虛擬機；④ 讓位模式內建。AutoPath 的輸入層 `autopath/bot/input.py` 設計成可替換的驅動（`postmessage` / `sendinput`），用設定切換，虛擬機方案沿用 `sendinput`。實測方法：在 AutoPath 加一個「背景點擊測試」按鈕，對遊戲視窗送 PostMessage 點擊並讀角色座標是否改變，結果寫入 log。

#### 6.7.9 目標遊戲：火炬之光：無限（公開資料對照，2026-11-25 查證）

> 標記說明：**[來源]** 為公開資料直接記載；**[推論]** 為依資料推測、尚未驗證；**[使用者]** 為使用者提供。

**遊戲基本資料**
- 開發／發行：XD Inc.；引擎：Unreal Engine 4；平台：Windows、Android、iOS；全球上線 2023-05-09 **[來源：[Wikipedia](https://en.wikipedia.org/wiki/Torchlight:_Infinite)]**
- 賽季制，約每季推出新的地圖玩法（例如 S12「Overrealm」新增隨機出現的裂隙與地圖詞綴）**[來源：[Pocket Gamer](https://www.pocketgamer.com/torchlight-infinite/season-12-overrealm-out-now)]**

**地圖流程與本設計的對照**

| 遊戲機制 | 資料 | 對設計的影響 |
|---|---|---|
| 進入地圖使用「信標（Beacon）」，綁定區域與時空刻度（Timemark） | **[來源：[TLI Wiki](https://www.torchlight-infinite.wiki/maps/torchlight-infinite-netherrealm-guide)、[Maxroll](https://maxroll.gg/torchlight-infinite/resources/progressing-the-netherrealm)]** | `select_map` 需要指定區域與時空刻度 |
| 信標每次進圖會消耗，用完可在城鎮用「夢境回響」購買 | **[推論：Maxroll／Wiki 的敘述暗示每輪消耗，未明文]** | **新增需求 R12**：每輪開始前確認信標數量，不足就停止並記錄（Bot 不自動購買） |
| 完成進度後會開放「羅盤（Compass）」欄位等額外投入 | **[來源：Maxroll]** | 符合使用者需求 3：Bot 不操作羅盤等額外欄位 |
| 每個區域的頭目為「界域守望者（Plane Watcher）」，需先清若干次關卡累積進度才能挑戰 | **[來源：TLI Wiki、Maxroll]** | 「頭目」步驟分兩種：一般關卡結尾的頭目、守望者；`kill_boss` 需能設定是否挑戰守望者 |
| 地圖佈局固定或隨機 | 查到的資料**沒有明確記載** **[使用者：固定生成]** | 維持 §6.7.8 A 的自動探索建圖，佈局即使是隨機也能運作 |
| 寵物（契靈）可自動拾取，設定 > 拾取 > 自動拾取；遊戲內建三級物品過濾 | **[來源：[Gamezebo](https://www.gamezebo.com/walkthroughs/torchlight-infinite-auto-loot/)]**；免費寵物無法撿全部物品 **[來源：同上]** | **調整**：撿物改為「靠寵物自動拾取＋遊戲過濾器」，Bot 只要在清怪後於掉落區停留／經過；不需要撿取鍵邏輯 |
| 遊戲沒有內建自動戰鬥或自動尋路 | **[來源：外掛販售商頁面 [musabots](https://musabots.com/blog/torchlight-infinite-bot)，非官方，僅供參考]** | — |
| 移動方式為滑鼠點擊、角色在畫面固定位置 | **[使用者]**（公開資料未查到） | 維持 §6.7.8 B 方向式點擊 |

**記憶體結構 [推論]**：UE4 遊戲的物件與名稱通常集中在引擎的全域物件表中，怪物、地面效果這類實體多半可以從那裡追到。這讓「實體清單」比較有機會找得到，但每次改版偏移都會變，所以 AOB 簽章與 §7 的維護流程更重要。需要用 GameRec 錄製後判讀確認。

**反外掛與服務條款（事實）**
- 官方公告（2022-10-14）：「任何使用第三方外掛的行為都會嚴重破壞公平健康的遊戲環境」，涉及帳號「將被封禁」，並會持續監控異常遊戲行為 **[來源：[TapTap 官方公告](https://www.taptap.io/post/1774531)]**
- 官方說明的主要封號原因包含「使用第三方掛機工具自動進行遊戲或獲利」**[來源：[VHPG 轉載的違規處理說明](https://www.vhpg.com/handling-of-violations-torchlight-infinite/)]**
- 有玩家回報只是使用 YoloMouse（游標美化工具）就收到「違禁軟體」通知 **[來源：[Steam 討論串](https://steamcommunity.com/app/1974050/discussions/0/3548301990162787798/)，玩家自述，非官方]** → **[推論]** 客戶端可能會偵測正在執行的第三方程式。
- 使用哪一套反外掛系統：查到的公開資料**沒有記載**（騰訊 ACE 的維基頁面未列出本遊戲）**[來源：[Wikipedia ACE](https://en.wikipedia.org/wiki/Anti-Cheat_Expert)]**

**對兩個程式的意義**
- GameRec 讀記憶體（開啟遊戲程序）、AutoPath 自動操作，都屬於官方明文禁止的「第三方外掛／掛機工具」範圍，在正式帳號上使用有封號風險；這點無法靠技術手段排除，本設計也不提供規避偵測的功能。
- 被動抓封包（Npcap 在網卡層抓取，不碰遊戲程序）、螢幕錄影對遊戲程序的接觸最少 **[推論，無官方資料]**，但仍屬第三方程式在同機執行。
- 背景點擊（PostMessage）是否被遊戲接受、虛擬機是否能執行遊戲，都需要實測 **[未驗證]**。

#### 6.7.10 使用者確認的玩法規則（2026-11-25 第二輪）

| # | 項目 | 使用者決定 | 設計對應 |
|---|---|---|---|
| 1 | 界域守望者 | 可挑戰時直接去打；挑戰難度由使用者在程式中設定 | `kill_boss` 新增 `watcher: {enabled: true, difficulty: <使用者設定>}`；GUI 提供難度選單 |
| 2 | 選哪個關卡 | 依混沌卡牌（Chaos Confusion Card）挑選**稀有度最高**的關卡 | 新增步驟 `pick_stage: {by: chaos_card_rarity, prefer: highest}`；卡牌稀有度的讀取依據（記憶體欄位或畫面上的卡牌框色）需錄製後判讀、使用者確認 |
| 3 | 賽季玩法 | 挑戰「黑帆」玩法；「征戰」玩法出現時自動開啟 | 新增 `season_events: {black_sail: engage, conquest: auto_open}`；黑帆的堆疊策略見下方 |
| 4 | 目標怪 | **只打黃名稀有怪** | `targeting.rare` 限定稀有（黃名）；魔法怪（藍名）與一般怪只在擋路時攻擊 |
| 5 | 近戰／遠程、交戰距離 | 依技能決定，錄製時提供適合方案 | 待錄製技能資料後，由 Claude 提出交戰距離與技能輪替方案供使用者選擇 |
| 6 | 補血 | 依技能決定，同上 | 同上，與 5 一起提出 |
| 7 | 背包滿 | 跳出提示 | 偵測背包已滿（依據經確認）→ 暫停 Bot、Windows 通知＋狀態列提示，等使用者處理 |
| 8 | 寵物撿不到的物品 | 只撿**高稀有度傳奇**與**通貨** | 撿物備援規則：只撿符合「傳奇且高稀有度」或「通貨」的掉落；物品類型與稀有度的判斷依據（掉落標籤顏色或記憶體欄位）需錄製後判讀、使用者確認 |

**黑帆玩法：四種堆疊方案（待交付）**
使用者要求在黑帆玩法的機制大致確認後，訂出 4 種選擇堆疊方案，目標分別為：
1. **追憶**（高稀有度）
2. **碎絮**
3. **游絲**
4. **首領**

做法：先蒐集黑帆玩法的公開資料，並請使用者錄製幾輪黑帆的選擇畫面與結果，由 Claude 判讀選項與效果，再整理成 4 份 `profiles/<game>/strategies/black_sail_<目標>.yaml`。AutoPath 在 GUI 中讓使用者選擇目前要用哪一份。在使用者確認之前，Bot 不會自行決定黑帆的選擇。

**環境與通知（2026-11-25 使用者確認）**

| # | 項目 | 使用者決定 | 設計對應 |
|---|---|---|---|
| 9 | 使用的帳號 | 直接用主帳號測試 | 使用者已知悉官方禁止第三方工具的封號風險（6.7.9），自行承擔；dry-run 模式仍建議先跑 |
| 10 | 畫面設定 | 視窗模式，**隨時可以縮放調整** | 見下方「視窗可縮放」 |
| 11 | 電腦規格 | 寫進**監控軟體 GameRec**，和環境檢測一起做，只掃描一次 | GameRec 環境檢測加入硬體規格掃描（CPU、記憶體、顯示卡、是否支援虛擬化），結果存檔供 AutoPath 判斷虛擬機方案；不重複掃描 |
| 12 | 停止時通知 | Windows 通知 | Bot 停止、暫停（背包滿、連續失敗、版本不符等）時發 Windows 通知，內容含原因；不串接外部服務 |
| 13 | 錄製時間 | 方向確定後才開始錄製 | 本設計文件確認後，依 6.7.6 錄製清單開始 |

**視窗可縮放（需求 10 的設計）**
- 所有螢幕座標都用「相對於遊戲視窗客戶區的比例」（0～1）儲存，不存絕對像素；每個 tick 重新讀取視窗位置與大小。
- 角色在畫面上的固定位置也存成比例，點擊半徑依視窗大小等比例縮放。
- 畫面範本比對（選地圖介面、混沌卡牌、掉落標籤）使用多尺度比對，依目前視窗大小縮放範本；比對信心不足就不點擊並記錄。
- 能用記憶體讀到的資訊優先用記憶體，因為不受視窗大小影響。
- 偵測到視窗大小在變動中時暫停輸入，穩定後再繼續。

#### 6.7.7 參考的開源專案（只參考設計，不直接複製程式碼；引用前會確認授權）

| 專案 | 參考重點 |
|---|---|
| [Koolo](https://beta.pkg.go.dev/github.com/hectorgimenez/koolo)（Go） | 讀記憶體取得世界狀態、地圖格網 A*、每條刷圖路線拆成 run 步驟 |
| [Kolbot](https://github.com/Fa-b/kolbot)（JavaScript） | 目標挑選與戰鬥設定檔（只打精英／略過特定怪／清完再打頭目） |
| [Botty](https://kandi.openweaver.com/python/aeon0/botty)（Python） | 畫面範本比對、Python 的路線流程與撿物規則 |
| [python-pathfinding](https://github.com/brean/python-pathfinding/blob/main/README.md) | 加權格網 A*（危險成本） |
| [py_trees](https://github.com/stonier/py_trees) | 行為樹的優先順序決策 |
| [Recast/Detour](https://www.GitHub.com/recastnavigation/recastnavigation) | 大地圖或 3D 地形時改用導航網格與動態障礙 |

---

## 7. 隨遊戲更新維護的方法

遊戲更新最常壞的是：**位址偏移**（幾乎每次）、**封包 opcode/格式**（有時）、**地圖**（偶爾）。對策如下：

### 7.1 偵測更新
- 啟動時計算 exe 版本與 SHA-256，與 profile 支援清單比對；不一致時提示「遊戲已更新」，Bot 進入鎖定，只允許錄製與分析。

### 7.2 位址：AOB 簽章自動重掃
- `autopath profile rescan <game>`：對每個簽章重新掃描，回報「找到 1 個／找到多個／找不到」。
- 找到唯一一個 → 重新解析並執行 `validate`；通過則自動更新 `memory.yaml` 的 `game_versions`。
- 找不到 → 自動產生**候選簽章**：用舊版本保存的「簽章周圍 64 bytes」做模糊比對（允許 ±N bytes 差異），列出相似度最高的位置給人工確認。
- 產生簽章的工具（`autopath profile make-sig`）：從一個已知位址附近抓指令，把相對位址、立即數自動換成 `??`，並驗證在模組中唯一。
- 每次通過的簽章結果與周圍 bytes 存入 `profiles/<game>/sigdb/<version>.json`，作為下次模糊比對依據。

### 7.3 封包：opcode 重新對應
- `regression/` 中每個動作保留一段短錄製。更新後在新版本重錄同樣動作（照「控制變因」腳本做 1～2 分鐘），把新舊兩份匯出檔一起交給 Claude 比對，判讀 opcode 是否變動；使用者確認後更新 `packets.yaml`。程式本身不自動配對。
- 因為 Bot 主要靠記憶體與鍵鼠，封包變動通常**不會讓 Bot 失效**，只影響「動作確認」這個輔助功能；可先停用封包確認繼續運作。

### 7.4 回歸測試
- `pytest -m regression`：用 `regression/` 中的錄製重播，驗證：
  - 位址解析後的數值與錄製時一致（座標軌跡、HP）。
  - 依 `actions.yaml` 照表轉換的動作名稱與錄製時一致。
  - 尋路在各地圖上能從 A 走到 B（純離線計算）。
- 遊戲更新 SOP 結束前必須全部通過。

### 7.5 更新 SOP（目標 1 小時內恢復）

1. 啟動 GameRec 或 AutoPath → 看到「版本不符」提示。
2. `autopath profile rescan` → 大部分簽章自動通過。
3. 失敗的簽章：看候選清單 → 用掃描器確認 → `make-sig` 重建。
4. 錄一段 2 分鐘的「標準動作腳本」→ 新舊匯出檔交給 Claude 比對封包 → 使用者確認後更新。
5. 跑回歸測試 → 全部通過。
6. 更新 `CHANGELOG.md`、`game_versions`，git commit（profile 有版本歷史，可隨時回復）。
7. Bot `--dry-run` 5 分鐘確認判讀正確 → 正式使用。

### 7.6 讓 Claude 協助維護
- profile 全是文字檔（YAML/JSON），回歸報告是 Markdown，可以直接交給 Claude 比對新舊差異、提出簽章與 opcode 修改建議（與現行「匯出 zip 給 Claude 分析」的流程一致）。

---

## 8. 程式模組規劃

### 8.1 程式一：GameRec（`gamerec/`，在現有程式上擴充）

| 模組 | 職責 | 主要套件 |
|---|---|---|
| `gamerec/annotate/keymap.py` | 依使用者的 `actions.yaml` 把按鍵照表轉為動作名稱（不推測），寫 `annotations.jsonl` | 標準庫 |
| `gamerec/annotate/ui.py` | 事後標記視窗（影片＋時間軸） | tkinter, opencv |
| `gamerec/fingerprint.py` | 遊戲版本指紋寫入 manifest | pefile（或 pywin32） |
| `gamerec/memory/watcher.py` | 監看清單新增 `signature` 型位址（透過 gamecommon 解析） | pymem |
| `gamerec/ui/app.py` | F5～F8 事件熱鍵、Ctrl+F9 區段、載入 profile 的 `memory.yaml` 當監看清單 | tkinter |

### 8.2 程式二：AutoPath（`autopath/`，新增）

| 模組 | 職責 | 主要套件 |
|---|---|---|
| `autopath/cli.py`、`autopath/ui/` | 三個模式：`view`、`profile`、`run`；GUI 先做「選 profile／路線 → dry-run → 開始／F12 停止」 | argparse, tkinter |
| `autopath/view/` | 時間軸對齊、原始資料整理（不做關聯判斷）、地圖建置 | pandas, numpy |
| `autopath/profile/sigtools.py` | 簽章重掃、產生、模糊比對 | 標準庫（必要時 capstone） |
| `autopath/bot/world.py` | WorldState 讀取 | pymem |
| `autopath/bot/fsm.py` | 狀態機與各 Behavior | 標準庫 |
| `autopath/bot/nav.py` | A*（含危險成本）、路點、平滑、脫困、未探索區域追蹤、地圖學習 overlay | numpy |
| `autopath/bot/hazard.py` | 危險成本圖、逃離安全格搜尋、閃避技能決策 | numpy |
| `autopath/bot/tree.py` | 行為樹（安全 → 閃避 → 補給 → 戰鬥 → 撿物 → 流程） | py_trees（或自寫） |
| `autopath/bot/steps.py` | 路線步驟：go_to / interact / select_map / clear / kill_boss / leave_map | 標準庫 |
| `autopath/bot/input.py` | SendInput 鍵鼠驅動、dry-run | ctypes |
| `autopath/bot/safety.py` | 強制停止（F12＋寫死的備用鍵）、看門狗、速率限制、版本鎖 | pynput |
| `autopath/ui/hotkeys.py` | 熱鍵設定視窗：錄製組合鍵、即時衝突檢查、滑鼠「恢復預設」 | tkinter |

### 8.3 共用函式庫（`gamecommon/`，不是程式）

| 模組 | 職責 |
|---|---|
| `gamecommon/session_format.py` | session zip 讀寫、`format_version` 相容處理 |
| `gamecommon/memtypes.py` | 型別編解碼（由現在的 `gamerec/memory/types.py` 移過來） |
| `gamecommon/aob.py` | AOB 簽章掃描與 RIP 相對位址解析 |
| `gamecommon/profile.py` | profile 讀取與 `validate` |
| `gamecommon/hotkeys.py` | 組合鍵解析與正規化、衝突與保留鍵驗證、預設值、設定檔讀寫（兩個程式共用） |

### 8.4 打包與發佈

- 兩個 PyInstaller spec：`GameRec.exe`、`AutoPath.exe`，各自版本號、各自 Release。
- 相容表寫在 README：AutoPath 版本 X 支援 session `format_version` 1～2、profile schema 1。

新增套件引入前會檢查來源、授權、維護狀態與已知漏洞（PyYAML 須使用 `safe_load`，避免載入任意物件）。

---

## 9. 風險與緩解

| 風險 | 影響 | 緩解 |
|---|---|---|
| 封包加密，看不懂內容 | 封包判讀價值降低 | 巡路只靠記憶體與鍵鼠；封包僅供參考 |
| 錯誤的對應關係被寫進設定檔 | Bot 誤判 | 程式不自動判斷；只用使用者確認過（`confirmed`）的項目 |
| 找不到實體清單（附近怪物/物品） | 無法精準選怪 | 先用「Tab 選怪鍵 + 目標 ID 記憶體」替代；或畫面偵測（後續） |
| 點地移動的座標換算不準 | 移動偏差 | 從錄製迴歸求矩陣，並以閉環修正 |
| 誤操作（狂按、亂點） | 角色死亡或物品損失 | dry-run 先行、速率上限、強制停止鍵（含不可改的備用鍵）、看門狗 |
| 遊戲更新造成位址錯誤 | Bot 行為錯亂 | 版本鎖 + validate，不符拒絕執行 |
| 服務條款／封號 | 帳號損失 | 僅用於單機/私服/測試帳號（見文件開頭） |

---

## 10. 建議下一步

1. **M2.5（程式一 GameRec 擴充）**：實作 `annotations.jsonl`、按鍵照表轉換、`F5`～`F8` 事件熱鍵、版本指紋；抽出 `gamecommon/`。
2. 使用者依 3.5 的建議錄 P2 資料，上傳 zip。
3. **M3（程式二 AutoPath 骨架＋檢視模式）**：`autopath view` 時間軸對齊與原始資料整理，配合 Claude 判讀流程，再做地圖建置。
4. **M4（AutoPath 設定檔工具＋巡路模式）**：profile loader、AOB 工具，接著 `autopath run --dry-run`，最後才開放實際輸入。
