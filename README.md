# discord-dm-bot

Miroボードの指定セクションを **Gemini CLI** で文字起こしし、**Discord DM** で送信するツール。

## 仕組み

1. **Playwright** でMiroボードをヘッドレスブラウザで開く
2. **Miro SDK** (`window.miro.board`) でボード上の全アイテムを取得
3. テキストが入っていないセクションはスクリーンショットを撮影
4. **Gemini CLI** で画像を文字起こし（OCR）
5. **Discord Bot** でDM送信

## セットアップ

### 必要なもの

- Python 3.9+
- Node.js + Playwright (`npm install playwright && npx playwright install chromium`)
- [Gemini CLI](https://github.com/google-gemini/gemini-cli) (`npm install -g @google/gemini-cli`)
- Discord Bot（DM送信権限あり）

### 環境変数

```bash
cp .env.example .env
# .env を編集して各値を設定
```

| 変数名 | 説明 |
|--------|------|
| `DISCORD_DM_BOT_TOKEN` | Discord Bot トークン |
| `DISCORD_DM_USER_ID` | 送信先ユーザーID |
| `GEMINI_API_KEY` | Gemini API キー |

## 使い方

### Miro文字起こし → DM送信

```bash
# .env を読み込み
source .env && export DISCORD_DM_BOT_TOKEN DISCORD_DM_USER_ID GEMINI_API_KEY

# セクション5と6を文字起こしてDM送信
python3 miro_ocr_dm.py "https://miro.com/app/board/uXjVG2v0xRw=/" "5,6"

# セクション1〜3を送信
python3 miro_ocr_dm.py "https://miro.com/app/board/uXjVG2v0xRw=/" "1,2,3"
```

### DM送信のみ

```bash
python3 discord_dm_send.py "メッセージ1" "メッセージ2"
```

## 注意事項

- Miroボードは「リンクを知る全員が閲覧可」に設定する必要があります
- Gemini API の無料枠にはレート制限があります（複数セクション時は自動で待機）
- Discordメッセージは2000文字制限があるため、長いセクションは分割される場合があります
