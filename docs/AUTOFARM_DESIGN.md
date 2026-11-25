# 自動跑圖／刷圖（AutoFarm）完整設計

> 本文件延續 `docs/ARCHITECTURE.md`（GameRec 記錄工具），說明從「採集資料」到「自動跑圖刷圖可實際使用」的完整流程、需求、資料標記規格、分析管線、Bot 架構，以及**隨遊戲更新維護**的方法。
>
> ⚠️ 對線上遊戲使用自動化腳本通常違反服務條款，可能封號。本設計以單機、自架私服或測試帳號為前提；**不包含**任何繞過或隱藏於反外掛偵測的設計。

---

## 0. 總覽

```
 ┌─────────── 階段 A：採集 ───────────┐   ┌──── 階段 B：分析 ────┐   ┌──── 階段 C：自動化 ────┐
 GameRec 錄製（記憶體/封包/影片/輸入）    gamerec-analyze           gamerec-bot
   + 動作標記（自動 + 手動）       ──►   對齊 → 事件切分 → 關聯   ──►  感知 → 狀態機 → 行動
   + 遊戲版本指紋                       → 封包欄位推斷 → 地圖建置       → A* 尋路 → 戰鬥/撿物迴圈
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
| **P3** | 封包／動作關聯分析 | `profiles/<game>/packets.yaml`：opcode 對照表與欄位定義；`analysis_report.md` | 主要動作（移動、攻擊、撿物）的 opcode 信心度 ≥ 0.9 |
| **P4** | 地圖建置 | `profiles/<game>/maps/<map_id>.json`：可走格網、傳送點、刷怪區、路點 | 在該地圖由錄製軌跡覆蓋 ≥ 80% 可走區 |
| **P5** | Bot 原型（只觀察） | `gamerec-bot --dry-run`：只讀狀態、規劃路徑、顯示「打算做什麼」，不送出輸入 | 狀態判讀與畫面一致率 ≥ 95% |
| **P6** | Bot 原型（閉環） | 用**模擬鍵鼠輸入**走路、打怪、撿物 | 指定地圖連續跑 30 分鐘無卡死，卡住能自動脫困 |
| **P7** | 穩定化 | 例外處理、死亡/背包滿/斷線處理、看門狗、完整紀錄 | 連續 4 小時無人工介入；所有停止原因皆有紀錄 |
| **P8** | 投入使用＋維護 | 版本偵測、簽章重掃工具、回歸測試、更新 SOP | 遊戲更新後依 SOP 在 1 小時內恢復 |

> 建議順序：P1→P2→P3 可以反覆迭代；P5 之前不送出任何輸入，降低誤操作風險。

---

## 2. 需求

### 2.1 功能需求

| 編號 | 需求 |
|---|---|
| F1 | GameRec 支援「動作標記」：自動（由輸入事件推斷）與手動（熱鍵/按鈕）兩種，並記錄動作開始與結束 |
| F2 | 每次錄製自動寫入遊戲版本指紋（exe 檔案版本、SHA-256、主要模組大小與時間戳） |
| F3 | 分析工具能把封包依 opcode 分群，並計算與動作標記、記憶體變化的時間關聯 |
| F4 | 分析工具能推斷封包欄位（座標、目標 ID、物品 ID）與記憶體數值的對應 |
| F5 | 由座標軌跡建立可走格網地圖與路點圖，支援手動編修 |
| F6 | Bot 有狀態機（巡邏/尋怪/戰鬥/撿物/補給/回城/脫困/停止） |
| F7 | Bot 以 A* 在格網上尋路，並以路點追蹤做閉環移動控制 |
| F8 | Bot 所有行為都可在 `--dry-run` 下只顯示不執行 |
| F9 | 所有遊戲相關位址、封包格式、按鍵配置都在 profile 設定檔中，程式碼不含寫死偏移 |
| F10 | 位址以 AOB 簽章為主、指標鏈為輔，啟動時自動解析並驗證 |
| F11 | Bot 執行過程可同時用 GameRec 錄製，作為除錯與回歸資料 |

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

---

## 3. GameRec 的標記規格擴充（讓後續腳本好寫）

現行 `markers.jsonl` 只有 `{t_ns, label}`。擴充為**結構化事件標記**，`format_version` 升為 2（向下相容：舊檔的 label 視為 `kind="note"`）。

### 3.1 `annotations.jsonl`（新增，取代 markers 的主要用途）

```json
{"id": 12, "t_start_ns": 15200000000, "t_end_ns": 15900000000,
 "kind": "action", "action": "attack", "source": "auto",
 "args": {"key": "1", "skill": "普攻"}, "target": {"mob_id": 3021},
 "confidence": 1.0, "tags": ["map:101", "combat"], "note": ""}
```

| 欄位 | 說明 |
|---|---|
| `id` | session 內唯一，方便互相參照 |
| `t_start_ns` / `t_end_ns` | 動作區間；瞬間事件兩者相同 |
| `kind` | `action`（玩家動作）、`event`（遊戲事件，如怪死亡、升級、換圖）、`state`（持續狀態，如戰鬥中）、`note`（自由備註） |
| `action` | 受控詞彙，見 3.2 |
| `source` | `auto`（由輸入推斷）、`hotkey`（玩家按標記熱鍵）、`manual`（事後在時間軸上補標）、`analyzer`（分析器推斷） |
| `args` | 動作參數：按鍵、技能名、點擊座標等 |
| `target` | 目標：怪物、NPC、物品、地圖座標 |
| `confidence` | 0～1；人工標記為 1，自動推斷 < 1 |
| `tags` | 自由標籤，建議 `map:<id>`、`area:<名稱>` |

### 3.2 動作詞彙表（`profiles/<game>/actions.yaml`）

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

自動標記器根據這張表，把 `input/events.jsonl` 的按鍵轉為 `action` 標記（按下到放開為一個區間）。玩家只需要手動標記**遊戲事件**（怪死了、撿到東西），或用熱鍵快速標記。

### 3.3 GUI／熱鍵擴充

| 熱鍵 | 功能 |
|---|---|
| `F9` | 一般標記（保留現行行為） |
| `F5`～`F8` | 可在設定中綁定常用事件（預設：怪死亡、掉落、撿到、換圖） |
| `Ctrl+F9` | 開始／結束一個「區段」，例如「這段是在刷 A 區」 |

另外新增「事後標記」視窗：拖動影片時間軸、看到對應記憶體與封包，點選補標，並可修正自動標記的錯誤。

### 3.4 版本指紋（寫入 `manifest.json`）

```json
"game_fingerprint": {
  "exe_file_version": "1.2.345.0",
  "exe_sha256": "ab12...",
  "modules": [{"name": "game.exe", "size": 48234496, "timestamp": 1764000000}],
  "profile": {"name": "mygame", "version": "2026.11.20", "signatures_ok": 9, "signatures_failed": 0}
}
```

### 3.5 其他採集建議（提高封包↔動作關聯品質）

- **刻意的單一動作錄製**：每段錄製只做一種動作（例如原地重複攻擊同一隻怪 20 次），中間停頓 1 秒以上，讓封包分群更乾淨。
- **控制變因**：走路時分別錄「只往東」、「只往北」，有助辨認座標欄位與方向欄位。
- **閒置基準**：每段開頭先閒置 10 秒，取得心跳/背景封包的基準，分析時扣除。
- **記憶體加錄**：除了座標，也監看目標 ID、背包數量、經驗值，因為它們的變化可直接當成事件「真值」來自動產生 `event` 標記（例如經驗值上升 → `mob_killed`）。

---

## 4. 分析管線（`gamerec-analyze`，新模組 `gamerec/analysis/`）

```
session zip(s) ─► 1 載入與對齊 ─► 2 自動事件標記 ─► 3 封包分群 ─► 4 時間關聯
                                                              │
     profile 產出 ◄─ 7 報告 ◄─ 6 地圖建置 ◄─ 5 欄位推斷 ◄────────┘
```

| 步驟 | 方法 | 輸出 |
|---|---|---|
| 1. 載入與對齊 | 所有串流轉成以 `t_ns` 為索引的 pandas DataFrame；影片幀用 `frames.csv` 對應 | `timeline.parquet` |
| 2. 自動事件標記 | 依 `actions.yaml` 把輸入轉為 action；依記憶體規則（經驗↑、地圖 ID 變、HP=0）產生 event | `annotations.jsonl`（`source=analyzer`） |
| 3. 封包分群 | 依前 1～4 bytes 當候選 opcode；長度分佈、方向分群；扣除閒置基準的週期封包 | `opcodes.csv`：opcode、方向、次數、長度、週期性 |
| 4. 時間關聯 | 對每個 action/event，統計其後 0～500ms 內各 opcode 出現機率 vs. 基準機率，計算 lift、precision、recall、延遲分佈 | `correlation.csv`：`action → opcode, lift, p, latency_ms` |
| 5. 欄位推斷 | 對已關聯的 opcode，逐 byte offset 嘗試 int16/32、float 解碼，和同時刻記憶體值（座標、目標 ID）算相關係數／完全相等比例；差分欄位（序號、時間戳）自動辨識 | `packets.yaml` 草稿（欄位名、型別、offset、信心度） |
| 6. 地圖建置 | 依地圖 ID 分組座標軌跡 → 格網化（例如 1 格 = 0.5 遊戲單位）→ 走過的格子為可走、膨脹 1 格後平滑；換圖點記為傳送點；`mob_killed` 位置熱點為刷怪區 | `maps/<id>.json`、熱度圖 png |
| 7. 報告 | Markdown 報告：關聯表、低信心項目、需要人工確認的清單、建議下一次要補錄什麼 | `analysis_report.md` |

> 封包若加密或壓縮，步驟 5 會失敗；此時以**記憶體狀態為主要感知來源**，封包僅用於時間關聯（知道「何時送出」即可驗證動作是否被伺服器接受）。這不影響 Bot 運作，因為 Bot 只讀記憶體、用鍵鼠輸入。

---

## 5. 遊戲設定檔（game profile）結構

```
profiles/<game>/
  profile.yaml        # 名稱、支援版本清單、視窗標題、時間設定
  memory.yaml         # 位址定義：AOB 簽章 + 指標鏈 + 型別 + 驗證規則
  packets.yaml        # opcode 與欄位定義（分析產出 + 人工確認）
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

## 6. Bot 架構（`gamerec-bot`，新模組 `gamerec/bot/`）

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
 Logging    │  bot_log.jsonl（決策/轉換/路徑）＋可同時啟動 GameRec 錄製      │
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

---

## 7. 隨遊戲更新維護的方法

遊戲更新最常壞的是：**位址偏移**（幾乎每次）、**封包 opcode/格式**（有時）、**地圖**（偶爾）。對策如下：

### 7.1 偵測更新
- 啟動時計算 exe 版本與 SHA-256，與 profile 支援清單比對；不一致時提示「遊戲已更新」，Bot 進入鎖定，只允許錄製與分析。

### 7.2 位址：AOB 簽章自動重掃
- `gamerec-profile rescan <game>`：對每個簽章重新掃描，回報「找到 1 個／找到多個／找不到」。
- 找到唯一一個 → 重新解析並執行 `validate`；通過則自動更新 `memory.yaml` 的 `game_versions`。
- 找不到 → 自動產生**候選簽章**：用舊版本保存的「簽章周圍 64 bytes」做模糊比對（允許 ±N bytes 差異），列出相似度最高的位置給人工確認。
- 產生簽章的工具（`gamerec-profile make-sig`）：從一個已知位址附近抓指令，把相對位址、立即數自動換成 `??`，並驗證在模組中唯一。
- 每次通過的簽章結果與周圍 bytes 存入 `profiles/<game>/sigdb/<version>.json`，作為下次模糊比對依據。

### 7.3 封包：opcode 重新對應
- `regression/` 中每個動作保留一段短錄製。更新後在新版本重錄同樣動作（照「控制變因」腳本做 1～2 分鐘），執行 `gamerec-analyze remap --old <舊 session> --new <新 session>`：依動作關聯與長度分佈自動配對舊 opcode → 新 opcode，產生差異報告。
- 因為 Bot 主要靠記憶體與鍵鼠，封包變動通常**不會讓 Bot 失效**，只影響「動作確認」這個輔助功能；可先停用封包確認繼續運作。

### 7.4 回歸測試
- `pytest -m regression`：用 `regression/` 中的錄製重播，驗證：
  - 位址解析後的數值與錄製時一致（座標軌跡、HP）。
  - 自動標記結果與已確認的標記一致（precision/recall 門檻）。
  - 尋路在各地圖上能從 A 走到 B（純離線計算）。
- 遊戲更新 SOP 結束前必須全部通過。

### 7.5 更新 SOP（目標 1 小時內恢復）

1. 啟動 GameRec → 看到「版本不符」提示。
2. `gamerec-profile rescan` → 大部分簽章自動通過。
3. 失敗的簽章：看候選清單 → 用掃描器確認 → `make-sig` 重建。
4. 錄一段 2 分鐘的「標準動作腳本」→ `remap` 封包。
5. 跑回歸測試 → 全部通過。
6. 更新 `CHANGELOG.md`、`game_versions`，git commit（profile 有版本歷史，可隨時回復）。
7. Bot `--dry-run` 5 分鐘確認判讀正確 → 正式使用。

### 7.6 讓 Claude 協助維護
- profile 全是文字檔（YAML/JSON），回歸報告是 Markdown，可以直接交給 Claude 比對新舊差異、提出簽章與 opcode 修改建議（與現行「匯出 zip 給 Claude 分析」的流程一致）。

---

## 8. 程式模組規劃（新增）

| 模組 | 職責 | 主要套件 |
|---|---|---|
| `gamerec/annotate/auto.py` | 輸入/記憶體 → 自動標記 | 標準庫 |
| `gamerec/annotate/ui.py` | 事後標記視窗（影片＋時間軸） | tkinter, opencv |
| `gamerec/fingerprint.py` | 遊戲版本指紋 | pefile（或 pywin32） |
| `gamerec/profile/loader.py` | 讀 profile、解析 AOB、驗證 | pymem, PyYAML |
| `gamerec/profile/sigtools.py` | 簽章掃描、產生、模糊比對 | 標準庫（必要時 capstone 反組譯） |
| `gamerec/analysis/` | 對齊、分群、關聯、欄位推斷、地圖建置、報告 | pandas, numpy |
| `gamerec/bot/world.py` | WorldState 讀取 | pymem |
| `gamerec/bot/fsm.py` | 狀態機與各 Behavior | 標準庫 |
| `gamerec/bot/nav.py` | A*、路點、平滑、脫困 | numpy |
| `gamerec/bot/input.py` | SendInput 鍵鼠驅動、dry-run | pywin32 / ctypes |
| `gamerec/bot/safety.py` | 停止熱鍵、看門狗、速率限制、版本鎖 | pynput |

新增套件引入前會檢查來源、授權、維護狀態與已知漏洞（PyYAML 須使用 `safe_load`，避免載入任意物件）。

---

## 9. 風險與緩解

| 風險 | 影響 | 緩解 |
|---|---|---|
| 封包加密，無法推斷欄位 | 封包分析價值降低 | 以記憶體為主；封包只做時間關聯 |
| 找不到實體清單（附近怪物/物品） | 無法精準選怪 | 先用「Tab 選怪鍵 + 目標 ID 記憶體」替代；或畫面偵測（後續） |
| 點地移動的座標換算不準 | 移動偏差 | 從錄製迴歸求矩陣，並以閉環修正 |
| 誤操作（狂按、亂點） | 角色死亡或物品損失 | dry-run 先行、速率上限、F12、看門狗 |
| 遊戲更新造成位址錯誤 | Bot 行為錯亂 | 版本鎖 + validate，不符拒絕執行 |
| 服務條款／封號 | 帳號損失 | 僅用於單機/私服/測試帳號（見文件開頭） |

---

## 10. 建議下一步

1. **M2.5（GameRec 擴充）**：實作 `annotations.jsonl`、自動標記器、`F5`～`F8` 事件熱鍵、版本指紋。（約 1～2 天）
2. 使用者依 3.5 的建議錄 P2 資料，上傳 zip。
3. **M3**：`gamerec-analyze` 步驟 1～4（關聯表）先做出來，再依結果做欄位推斷與地圖建置。
4. **M4**：profile loader + AOB 工具，接著 Bot dry-run。
