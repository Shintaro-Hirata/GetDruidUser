# セットアップガイド

本ツール（Druid Query Runner）を利用するための環境構築手順です。

---

## 前提条件

- **Python 3.10 以上**がインストールされていること
- **Google Cloud（GCP）**の対象プロジェクト（`t2-integration`）へのアクセス権があること

---

## 0. フォルダの配置場所（重要）

ツールのフォルダは、**パスに日本語や特殊文字を含まない場所**に置いてください。

**NG な例:**
```
C:\Users\田中\デスクトップ\GetDruidUser
C:\Users\Tanaka\OneDrive – 株式会社○○\GetDruidUser
```

**OK な例:**
```
C:\Tools\GetDruidUser
C:\Users\Tanaka\GetDruidUser
D:\work\GetDruidUser
```

> **なぜ？** Windows のバッチファイル（`.bat`）は、パスに日本語・全角文字・特殊記号（`–` など）が含まれると正しく動作しないことがあります。
> 特に OneDrive の同期フォルダ名に会社名（日本語）が入っている場合に問題になりやすいです。
>
> **対処法:** フォルダごと `C:\Tools\GetDruidUser` など英数字のみのパスにコピーまたは移動してください。

---

## 1. Python パッケージのインストール

```bash
pip install -r requirements.txt
```

主な依存パッケージ:
- `streamlit` — Web UI フレームワーク
- `google-cloud-bigquery[pandas]` — BigQuery クライアント
- `pandas`, `matplotlib`, `openpyxl` — データ処理・可視化・Excel出力

---

## 2. Google Cloud SDK（gcloud）のセットアップ

### 2-1. Google Cloud SDK のインストール

既に `gcloud` コマンドが使える場合はこの手順はスキップしてください。

#### Windows

1. [Google Cloud SDK インストーラ](https://cloud.google.com/sdk/docs/install?hl=ja) からインストーラをダウンロード
2. インストーラを実行（デフォルト設定のままでOK）
3. インストール完了後、「Google Cloud SDK Shell」が使えるようになります

#### macOS

```bash
# Homebrew を使う場合
brew install --cask google-cloud-sdk
```

または [公式ページ](https://cloud.google.com/sdk/docs/install?hl=ja) からダウンロード。

#### Linux

```bash
# Debian/Ubuntu の場合
sudo apt-get install -y google-cloud-cli

# または公式スクリプト
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
```

### 2-2. gcloud の初期設定

本ツールは BigQuery からデータを取得するため、「どの Google アカウントで」「どのプロジェクトに」アクセスするかを PC に記憶させる必要があります。以下の初期設定は、そのための手順です（初回のみ）。

```bash
gcloud init
```

対話形式で以下を聞かれます:
1. **Googleアカウントでログイン** → ブラウザが開くのでログイン（認証完了後、コマンドプロンプトに戻ってください）
2. **プロジェクトの選択** → `t2-integration` を選択（リストに出ない場合は手入力）
3. **Compute Region/Zone の設定** → `n` を入力してスキップ（本ツールでは不要です）

### 2-3. アプリケーションデフォルト認証の設定

**これが最も重要な手順です。** `gcloud init` は gcloud コマンド自体の設定ですが、本ツールのような Python アプリケーションから BigQuery にアクセスするには、別途「アプリケーション用の認証情報」が必要です。以下のコマンドで、その認証情報を PC に保存します。

```bash
gcloud auth application-default login
```

ブラウザが開くので Google アカウントでログインしてください。
完了すると、以下のようなメッセージが表示されます:

```
Credentials saved to file: [/home/<user>/.config/gcloud/application_default_credentials.json]
```

> **補足**: この方法なら `GOOGLE_APPLICATION_CREDENTIALS` 環境変数の手動設定は不要です。

---

## 3. GCP 権限の確認

対象プロジェクト `t2-integration` で以下のロールが付与されている必要があります:

| ロール | 役割 |
|--------|------|
| `roles/bigquery.dataViewer` | BigQuery データ閲覧者（テーブルの読み取り） |
| `roles/bigquery.jobUser` | BigQuery ジョブユーザー（クエリの実行） |

権限が無い場合は、プロジェクト管理者に付与を依頼してください。

---

## 4. アプリケーションの起動

```bash
streamlit run app.py
```

ブラウザが自動で開き、ツールの画面が表示されます（デフォルト: http://localhost:8501）。

---

## トラブルシューティング

### 「Failed to initialize BigQuery client」と表示される

- `gcloud auth application-default login` を実行済みか確認
- ネットワーク（VPN/プロキシ）の接続を確認

### 「Access Denied」エラー

- 必要な GCP 権限（上記のロール）が付与されているか、プロジェクト管理者に確認

### プロキシ環境で接続できない

環境変数を設定してください:

```bash
export HTTPS_PROXY=http://your-proxy:port
export HTTP_PROXY=http://your-proxy:port
```

### gcloud コマンドが見つからない

- ターミナルを再起動してください（PATH が通っていない場合があります）
- Windows の場合は「Google Cloud SDK Shell」から実行してください

---

## クイックスタート（まとめ）

```bash
# 1. パッケージインストール
pip install -r requirements.txt

# 2. GCP認証（初回のみ・ブラウザが開きます）
gcloud auth application-default login

# 3. 起動
streamlit run app.py
```
