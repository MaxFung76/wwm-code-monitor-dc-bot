# WWM Discord 兌換碼監控 Bot

這個專案會定期監控巴哈姆特指定文章，抓取兌換碼並區分有效/過期狀態，然後同步到 Discord 頻道與 SQLite。

## 已完成功能

- 監控巴哈文章 `snA=388`
- 辨識 `<strike>`、`<s>`、`<del>` 內的過期碼
- 將結果寫入 SQLite，並只通知新出現的有效碼
- Discord 面板按鈕：
  - `新增兌換碼`
  - `查詢當月列表`
- 頻道文字訊息監聽，自動抓取成員貼上的代碼
- 自動置底：每次互動或新訊息後刪除舊面板並重發到最下方

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

## 測試

```bash
pytest
```

## 後續可擴充

- 改用 Supabase 或 GitHub Issue 做狀態同步
- 加入管理員限定按鈕權限
- 對手動新增與監控新增分開頻道通知
- 增加 slash commands 與排程健康檢查
