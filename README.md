# WWM Discord 兌換碼監控 Bot

這個專案會定期監控巴哈姆特指定文章，抓取兌換碼並區分有效/過期狀態，然後同步到 Discord 頻道與 SQLite。

目前系統已同時支援兩種資料來源模式：

- `live_bahamut`：Bot 直接從 VPS 抓巴哈文章
- `github_snapshot`：GitHub Actions 先抓巴哈並產出 snapshot，VPS Bot 再讀 snapshot

如果你的 VPS 直連巴哈常遇到 `403 Forbidden`、`系統維修中`、或內容抓不到文章，建議正式環境以 `github_snapshot` 為主。

## 系統架構

### 主要元件

- `Discord Bot`
  - 提供面板按鈕、Slash Commands、訊息監聽
  - 負責將監控結果寫入 SQLite 與發送 Discord 訊息
- `SQLite`
  - 儲存目前每個兌換碼的最終狀態
  - 保留觀測紀錄與面板訊息狀態
- `Bahamut Parser`
  - 解析巴哈文章 HTML
  - 以 `<strike>` / `<s>` / `<del>` 判定過期碼
- `GitHub Actions Snapshot`
  - 定期在 GitHub runner 抓巴哈文章
  - 產生 `bahamut_snapshot.json`
  - 發佈到 `snapshot-cache` 分支供 VPS 讀取
- `Watchtower`
  - 當 GHCR 有新版 image 時，自動拉下並重啟 bot

### 推薦正式流程

```text
GitHub Actions
  -> 抓巴哈文章
  -> 產生 bahamut_snapshot.json
  -> 發佈到 snapshot-cache 分支

VPS Bot
  -> 讀取 REMOTE_SNAPSHOT_URL
  -> reconcile 到 SQLite
  -> Discord 公告新有效碼 / 更新面板
```

### 狀態來源優先順序

- `monitor`
  - 代表來自巴哈監控或 GitHub snapshot
  - 適合當成最終真實狀態來源
- `message`
  - 代表使用者在頻道內貼出的碼
  - 可先收錄，但之後若 monitor 判定為 expired，會被覆蓋
- `manual`
  - 代表透過面板手動新增
  - 一樣會被後續 monitor 結果覆蓋

## 已完成功能

- 監控巴哈文章 `snA=388`
- 辨識 `<strike>`、`<s>`、`<del>` 內的過期碼
- 將結果寫入 SQLite，並只通知新出現的有效碼
- Discord 面板按鈕：
  - `新增兌換碼`
  - `查詢當月列表`
- Slash Commands：
  - `/setup_buttons`
  - `/sync_now`
- 頻道文字訊息監聽，自動抓取成員貼上的代碼
- 自動置底：每次互動或新訊息後刪除舊面板並重發到最下方

## 快速上手

如果你是第一次接手這個專案，最建議照這個順序做：

1. push 專案到 GitHub
2. 開啟 GHCR image build
3. 開啟 `Publish Bahamut Snapshot`
4. 在 VPS 設定 `.env`
5. 啟動 `wwm-codebot` 與 `watchtower`
6. 在 Discord 執行 `/setup_buttons`
7. 執行 `/sync_now` 驗證模式是否為 `github_snapshot`

## 專案結構

```text
src/wwm_codebot/
  bahamut.py       # 巴哈文章抓取與 HTML 解析
  config.py        # 環境變數設定
  discord_bot.py   # Discord Bot、按鈕面板、訊息監聽
  main.py          # 啟動入口
  models.py        # 資料模型
  storage.py       # SQLite 狀態同步與查詢
tests/
  test_bahamut_parser.py
```

## 安裝

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

## 設定

1. 複製 `.env.example` 為 `.env`
2. 填入以下內容：

```env
DISCORD_TOKEN=your-discord-bot-token
DISCORD_CHANNEL_ID=123456789012345678
FORUM_URL=https://forum.gamer.com.tw/C.php?bsn=75703&snA=388
REMOTE_SNAPSHOT_URL=
DATABASE_PATH=data/redeem_codes.db
MONITOR_INTERVAL_MINUTES=10
REQUEST_TIMEOUT_SECONDS=20
```

### 建議正式環境設定

如果 VPS 抓巴哈不穩，`.env` 建議至少長這樣：

```env
DISCORD_TOKEN=your-discord-bot-token
DISCORD_CHANNEL_ID=123456789012345678
DISCORD_GUILD_ID=123456789012345678
FORUM_URL=https://forum.gamer.com.tw/C.php?bsn=75703&snA=388
REMOTE_SNAPSHOT_URL=https://raw.githubusercontent.com/<owner>/<repo>/snapshot-cache/bahamut_snapshot.json
DATABASE_PATH=data/redeem_codes.db
MONITOR_INTERVAL_MINUTES=10
REQUEST_TIMEOUT_SECONDS=20
USE_REGISTRY_IMAGE=true
IMAGE_NAME=ghcr.io/<owner>/<repo>:latest
```

### 重要環境變數說明

- `DISCORD_TOKEN`
  - Discord Bot Token
- `DISCORD_CHANNEL_ID`
  - 面板預設頻道
  - 若之後執行 `/setup_buttons`，bot 會把目前頻道記成新的面板頻道
- `DISCORD_GUILD_ID`
  - 讓 slash command 同步到指定伺服器
  - 有設定時，`/setup_buttons` 與 `/sync_now` 會比較快出現
- `FORUM_URL`
  - 巴哈文章 URL
- `REMOTE_SNAPSHOT_URL`
  - 若有設定，bot 會優先讀這份 JSON，不再由 VPS 直接抓巴哈
- `DATABASE_PATH`
  - SQLite 檔案位置
- `MONITOR_INTERVAL_MINUTES`
  - Bot 自己執行同步的頻率
- `REQUEST_TIMEOUT_SECONDS`
  - HTTP / browser 抓取 timeout
- `USE_REGISTRY_IMAGE`
  - `true` 表示 VPS 走 GHCR image 模式
- `IMAGE_NAME`
  - image 名稱，建議用 `ghcr.io/<owner>/<repo>:latest`

## Discord Bot 權限

請在 Discord Developer Portal 啟用：

- `MESSAGE CONTENT INTENT`

Bot 進伺服器時至少要有：

- 讀取訊息
- 發送訊息
- 管理訊息
- 讀取訊息歷史

`管理訊息` 用於自動刪除舊面板並重新發送。

## 啟動

```bash
python -m wwm_codebot.main
```

## 使用方式

### Discord 面板

- `新增兌換碼`
  - 開啟 Modal 輸入一筆或多筆代碼
- `查詢當月列表`
  - 顯示本月已收錄碼清單
  - 若資料太多，會自動截斷並顯示「其餘 X 筆未顯示」

### Slash Commands

- `/setup_buttons`
  - 在目前頻道重發面板
  - 也會把這個頻道記成新的面板頻道
- `/sync_now`
  - 立刻執行一次同步
  - 可加 `code` 參數確認特定代碼狀態

範例：

```text
/sync_now code:AC46AQH368
```

若同步成功，回覆通常會包含：

- `mode: github_snapshot` 或 `mode: live_bahamut`
- `snapshot[CODE]: active/expired/not found`
- `db[CODE]: active/expired (source_type)`

### 頻道貼碼

- 成員直接在面板頻道貼上文字
- Bot 會自動從文字中抓出代碼
- 若是新有效碼，會立即公告
- 然後把面板重新置底

## 雲端部署

建議目標環境：`Ubuntu 24.04 VPS + Docker Compose`

### 1. 安裝 Docker

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
```

### 2. 放置專案

```bash
git clone <your-repo-url> wwm-codebot
cd wwm-codebot
mkdir -p data
cp .env.example .env
```

### 3. 設定 `.env`

至少填入：

```env
DISCORD_TOKEN=your-discord-bot-token
DISCORD_CHANNEL_ID=123456789012345678
FORUM_URL=https://forum.gamer.com.tw/C.php?bsn=75703&snA=388
REMOTE_SNAPSHOT_URL=
MONITOR_INTERVAL_MINUTES=10
REQUEST_TIMEOUT_SECONDS=20
USE_REGISTRY_IMAGE=false
IMAGE_NAME=wwm-codebot:local
```

`docker-compose.yml` 會自動把資料庫路徑設成 `/app/data/redeem_codes.db`，並把主機上的 `./data` 掛進容器，所以 SQLite 會持久化保留。

如果你的 VPS 直連巴哈容易遇到 `403` 或維護頁，建議改用 GitHub Actions 產生 snapshot，再讓 VPS 讀取：

```env
REMOTE_SNAPSHOT_URL=https://raw.githubusercontent.com/<owner>/<repo>/snapshot-cache/bahamut_snapshot.json
```

設了 `REMOTE_SNAPSHOT_URL` 後，bot 會優先讀這份 snapshot，不再由 VPS 直接抓巴哈文章。

### 4. 使用 `deploy.sh` 一鍵部署

```bash
chmod +x deploy.sh
./deploy.sh
```

腳本會自動執行：

- `git fetch`
- `git checkout main`
- `git pull --ff-only`
- `docker compose up -d --build --remove-orphans`

如果你之後改成 registry 映像模式，可以在 `.env` 設定：

```env
USE_REGISTRY_IMAGE=true
IMAGE_NAME=ghcr.io/<owner>/<repo>/wwm-codebot:latest
```

此時 `deploy.sh` 會改走：

- `docker compose pull wwm-codebot`
- `docker compose up -d --remove-orphans`

### 5. 直接啟動容器

```bash
docker compose up -d --build
```

### 6. 查看狀態與日誌

```bash
docker compose ps
docker compose logs -f
```

### 7. 更新版本

```bash
./deploy.sh
```

### 8. SQLite 備份

資料庫實體位置在主機的：

```text
./data/redeem_codes.db
```

可直接備份：

```bash
cp data/redeem_codes.db data/redeem_codes.db.bak
```

### 9. Watchtower 自動更新

`docker-compose.yml` 已經包含 `watchtower` 服務，啟動後每 5 分鐘檢查一次有沒有新映像。

```bash
docker compose up -d watchtower
docker compose logs -f watchtower
```

目前 `watchtower` 只會更新有加上 `com.centurylinklabs.watchtower.enable=true` 標籤的服務，也就是 `wwm-codebot`。

注意：

- 若你目前使用 `USE_REGISTRY_IMAGE=false` 的本機 build 模式，`watchtower` 不會因 Git 原始碼更新而自動重建容器。
- `watchtower` 最適合搭配 registry 映像模式，例如 `ghcr.io/...:latest`。
- 如果你是走本機 build 模式，建議仍以 `./deploy.sh` 或 GitHub Actions SSH 部署為主。

#### 搭配 GHCR（推薦的全自動更新模式）

專案已提供 GHCR 映像建置 workflow： [.github/workflows/ghcr-image.yml](file:///d:/Trae/WWM-DC-BOT/.github/workflows/ghcr-image.yml)

當你 push 到 `main` 後，GitHub Actions 會建置並推送映像：

- `ghcr.io/<owner>/<repo>:latest`
- `ghcr.io/<owner>/<repo>:<sha>`

VPS `.env` 建議設定：

```env
USE_REGISTRY_IMAGE=true
IMAGE_NAME=ghcr.io/<owner>/<repo>:latest
```

之後更新流程會變成：

- push 到 GitHub 觸發 GHCR build/push
- watchtower 自動拉新映像並重啟 `wwm-codebot`

GitHub repo 設定需確認：

- `Settings > Actions > General > Workflow permissions` 設定為 `Read and write permissions`
- GHCR 映像名稱要求全小寫；本專案 workflow 會自動轉成小寫推送到 `ghcr.io/<owner>/<repo>:latest`

VPS 端若遇到 watchtower `403 Forbidden` / `auth not present`，通常代表 GHCR 套件是 private，需要登入憑證。建議做法：

1. 建立 GitHub PAT：
   - scopes: `read:packages`
   - 若 repo 是 private，通常也需要 `repo`
2. 在 VPS 專案目錄建立 docker config 並登入（credentials 會存到 `./.docker/config.json`）：

```bash
mkdir -p .docker
DOCKER_CONFIG=./.docker docker login ghcr.io -u <github-username>
```

3. 重啟 watchtower：

```bash
docker compose up -d --remove-orphans watchtower
docker compose logs -f watchtower
```

或是把 GHCR package 設為 public（Packages → Package settings → Change visibility），就不需要登入。

### 10. GitHub Actions 自動 SSH 部署

已新增 workflow： [.github/workflows/deploy.yml](file:///d:/Trae/WWM-DC-BOT/.github/workflows/deploy.yml)

觸發條件：

- 手動執行 `workflow_dispatch`

請在 GitHub repository secrets 設定以下值：

- `VPS_HOST`：主機 IP 或網域
- `VPS_USER`：SSH 使用者
- `VPS_SSH_KEY`：部署私鑰內容
- `VPS_PORT`：SSH port，通常是 `22`
- `VPS_APP_DIR`：專案在主機上的路徑，例如 `/opt/wwm-codebot`

Workflow 會透過 SSH 進入主機後執行：

```bash
cd <VPS_APP_DIR>
chmod +x deploy.sh
BRANCH=main ./deploy.sh
```

建議首次上線時，先手動在主機完成以下動作一次：

- 安裝 Docker / Compose
- `git clone` 專案
- 建立 `.env`
- 建立 `data/`

之後再交給 GitHub Actions 自動部署。

### 11. GitHub Actions 抓巴哈 Snapshot

已新增 workflow： [.github/workflows/bahamut-snapshot.yml](file:///d:/Trae/WWM-DC-BOT/.github/workflows/bahamut-snapshot.yml)

用途：

- 每 10 分鐘在 GitHub runner 抓一次巴哈文章
- 產生 `bahamut_snapshot.json`
- 發佈到 `snapshot-cache` 分支

#### 更新頻率說明

目前 workflow 設定是：

```yaml
schedule:
  - cron: "*/10 * * * *"
```

代表大約每 10 分鐘跑一次，也就是：

- `00`
- `10`
- `20`
- `30`
- `40`
- `50`

注意：

- GitHub Actions 的排程不保證秒級準時
- 實際可能會延遲幾分鐘
- 若你要立即更新，可手動按 `Run workflow`

第一次使用建議：

1. 到 GitHub repo：
   - `Settings > Actions > General > Workflow permissions`
   - 設成 `Read and write permissions`
2. 手動執行一次 `Publish Bahamut Snapshot`
3. 成功後把 VPS `.env` 設成：

```env
REMOTE_SNAPSHOT_URL=https://raw.githubusercontent.com/<owner>/<repo>/snapshot-cache/bahamut_snapshot.json
```

以你目前 repo 為例：

```env
REMOTE_SNAPSHOT_URL=https://raw.githubusercontent.com/MaxFung76/wwm-code-monitor-dc-bot/snapshot-cache/bahamut_snapshot.json
```

4. 重啟 VPS bot：

```bash
docker compose restart wwm-codebot
```

之後 `/sync_now` 與排程監控都會走 `github_snapshot` 模式。

#### 如何確認 snapshot 模式已生效

1. 確認 raw URL 可以打開：

```text
https://raw.githubusercontent.com/<owner>/<repo>/snapshot-cache/bahamut_snapshot.json
```

2. 確認 VPS `.env` 已設：

```env
REMOTE_SNAPSHOT_URL=https://raw.githubusercontent.com/<owner>/<repo>/snapshot-cache/bahamut_snapshot.json
```

3. 重啟 bot：

```bash
docker compose restart wwm-codebot
```

4. 在 Discord 執行：

```text
/sync_now code:AC46AQH368
```

如果回覆裡有：

- `mode: github_snapshot`

就代表 VPS 已經不再直接抓巴哈。

### 12. 常用指令

- `/setup_buttons`：在目前頻道重新發送面板，並記住該頻道作為面板/監聽頻道
- `/sync_now`：立刻同步巴哈文章並更新狀態（可選填特定 code 來檢查 status）

### 13. 部署注意事項

- 這是 Discord Bot，不需要開放 HTTP port。
- 請確認 VPS 出站網路可連到 `discord.com` 與 `forum.gamer.com.tw`。
- 若你要自動重開機恢復，`restart: unless-stopped` 已經會自動拉起容器。
- 若同時開啟 `watchtower` 與 GitHub Actions SSH 部署，建議明確區分用途：
  - `GitHub Actions` 負責原始碼更新後重建或拉新版本
  - `watchtower` 負責 registry 映像有新版時自動套用
- 若之後要搬到 Supabase，只需要替換 `storage.py` 的資料層，不影響 Docker 部署方式。

## 日常維運

### 最常用的 6 個檢查

```bash
docker compose ps
docker compose logs -f wwm-codebot
docker compose logs -f watchtower
docker inspect wwm-codebot --format '{{.Config.Image}}'
docker ps -a --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"
cat .env | grep REMOTE_SNAPSHOT_URL
```

### 判斷更新是否成功

- GitHub Actions `Build and Push GHCR Image` 成功
- `watchtower` log 顯示找到新 image 並重啟 `wwm-codebot`
- `docker inspect wwm-codebot --format '{{.Image}}'` 的 digest 改變
- bot logs 有：
  - `Guild commands synced: ...`
  - `Logged in as ...`

### 判斷 snapshot 是否成功

- `Publish Bahamut Snapshot` workflow 成功
- raw URL 可正常開啟 JSON
- `/sync_now` 顯示 `mode: github_snapshot`

### 發生問題時的建議排查順序

1. 先看 `docker compose ps`
2. 再看 `docker logs --tail=200 wwm-codebot`
3. 確認 `REMOTE_SNAPSHOT_URL` 是否存在
4. 確認 GitHub snapshot raw URL 是否可打開
5. 確認 GHCR image 是否有更新
6. 最後才回頭檢查 Discord 權限或巴哈抓取

### 14. 常見錯誤排除

#### sqlite3.OperationalError: unable to open database file

這通常是 `./data` 掛載進容器後，權限不足導致容器使用者無法寫入。

在 VPS 專案目錄下執行：

```bash
mkdir -p data
sudo chown -R 1000:1000 data
```

再重啟：

```bash
docker compose up -d --build --remove-orphans
```

如果你曾用 `sudo` 建立 `data/`，很容易變成 root 擁有，導致容器內無法寫入。

#### watchtower: client version 1.25 is too old (Minimum supported API version is 1.40)

這是 Docker API 版本協商問題（watchtower 透過 `/var/run/docker.sock` 呼叫 daemon 時，被要求至少使用 API 1.40）。

已在 `docker-compose.yml` 的 `watchtower` 服務加入：

```yaml
environment:
  DOCKER_API_VERSION: "1.40"
```

套用更新：

```bash
docker compose up -d --build --remove-orphans
docker compose logs -f watchtower
```

如果仍然出現相同錯誤，請在 VPS 上提供以下輸出以定位是 daemon 類型或版本不相容：

```bash
docker version
docker info
```

#### Discord 按鈕顯示「該申請未受回應」

常見原因：

- Bot 沒有在 3 秒內回應 interaction
- 按到舊面板
- 回覆內容超過 Discord 限制

目前系統已處理：

- 按鈕先 `defer`
- 月清單超過 2000 字會自動截斷

如果仍發生，請看：

```bash
docker compose logs --since=2m wwm-codebot
```

#### Discord 回覆 `Invalid Form Body` / `Must be 2000 or fewer in length`

這代表訊息超過 Discord 2000 字限制。

目前 `查詢當月列表` 已內建截斷邏輯；如果你仍看到這個錯，通常代表 VPS 還沒更新到最新 image。

請執行：

```bash
docker compose pull wwm-codebot
docker compose up -d --remove-orphans
docker compose restart wwm-codebot
```

#### 巴哈在 VPS 上一直是 403 或系統維修中

若 VPS 直連巴哈一直失敗，請不要再依賴 `live_bahamut`。

改用：

- GitHub Actions `Publish Bahamut Snapshot`
- VPS `.env` 設定 `REMOTE_SNAPSHOT_URL`

只要 `/sync_now` 顯示：

- `mode: github_snapshot`

就代表已成功繞過 VPS 對巴哈的連線問題。

## 測試

```bash
pytest
```

## 後續可擴充

- 改用 Supabase 或 GitHub Issue 做狀態同步
- 加入管理員限定按鈕權限
- 對手動新增與監控新增分開頻道通知
- 增加 slash commands 與排程健康檢查
