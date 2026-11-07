import json
import os
import re
import time
import urllib.request
from datetime import datetime

# ==========================================
# 設定項目
# ==========================================
# 監視したいX(旧Twitter)のユーザーID（@を除いた文字列）
TARGET_USERS = [
    "ZZZ_JP",
]

# 環境変数からDiscordのWebhook URLを取得（GitHub Secrets等から渡す）
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# 取得済みのツイートIDを記録するファイル名
STATE_FILE = "last_tweets.json"


def load_state() -> dict:
    """前回どこまで通知したかの状態を読み込む"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] 状態ファイルの読み込みに失敗しました: {e}", flush=True)
    return {}


def save_state(state: dict) -> None:
    """通知済みの最新ツイートIDを保存する"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 状態ファイルの保存に失敗しました: {e}", flush=True)


def fetch_user_tweets(username: str) -> list[dict]:
    """Jina Reader経由でXのプロフィールページから最新ポストを取得する（古い順にソートして返す）"""
    url = f"https://r.jina.ai/https://x.com/{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "X-No-Cache": "true",
        "X-With-Links-Summary": "true",
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8", errors="ignore")

            # 1) https://x.com/<username>/status/<id> の形式
            pattern_status = rf"https://x\.com/{re.escape(username)}/status/(\d+)"
            ids_status = re.findall(pattern_status, body, flags=re.IGNORECASE)

            # 2) https://x.com/compose/post?in_reply_to=<id> の形式（各ポストのリプライボタンに含まれるID）
            pattern_reply = r"in_reply_to=(\d+)"
            ids_reply = re.findall(pattern_reply, body, flags=re.IGNORECASE)

            unique_ids = list(dict.fromkeys(ids_status + ids_reply))

            tweets = []
            for tid in unique_ids:
                # Discord上でカード表示（本文・画像・動画の自動展開）が綺麗にされるよう fxtwitter.com のURLに変換
                fx_url = f"https://fxtwitter.com/{username}/status/{tid}"
                tweets.append(
                    {
                        "id": tid,
                        "username": username,
                        "url": fx_url,
                    }
                )

            # ツイートのID順（古い順）に並び替え
            tweets.sort(key=lambda x: int(x["id"]))
            return tweets

    except Exception as e:
        print(f"[WARN] @{username} のフィード取得に失敗しました: {e}", flush=True)
        return []


def send_to_discord(tweet: dict) -> bool:
    """Discord Webhookにツイートを送信する"""
    username = tweet["username"]
    # fxtwitterのURLを送ることで、Discord側が自動で本文・画像・動画を綺麗に展開します
    content = f"📢 **@{username}** の新規ポスト\n{tweet['url']}"

    payload = {
        "content": content,
        "username": f"X通知 (@{username})",
    }

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (https://discord.com, 1.0)",
    }

    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status in (200, 204)
    except Exception as e:
        print(f"[ERROR] Discordへの送信に失敗しました: {e}", flush=True)
        return False


def check_and_notify():
    """全ターゲットユーザーの新規ツイートを確認し、あればDiscordへ通知する"""
    state = load_state()

    for username in TARGET_USERS:
        tweets = fetch_user_tweets(username)
        if not tweets:
            print(f"[WARN] @{username} のツイート一覧が空でした。", flush=True)
            continue

        last_id = state.get(username)

        # 初回実行時は動作確認を兼ねて最新1件をDiscordへ通知し、そのIDを記録する
        if last_id is None:
            latest_tweet = tweets[-1]
            print(
                f"[INFO] @{username} の初回登録です。最新ポスト1件をDiscordに送信します: {latest_tweet['url']}",
                flush=True,
            )
            if send_to_discord(latest_tweet):
                state[username] = latest_tweet["id"]
                save_state(state)
                print(
                    f"[INFO] @{username} の初回通知を完了しました（最新ID: {latest_tweet['id']}）。以降は新規ポストのみ通知します。",
                    flush=True,
                )
            continue

        # 前回記録したIDより新しいツイートを抽出（固定ポスト等の古いIDは無視される）
        new_tweets = [t for t in tweets if int(t["id"]) > int(last_id)]

        if not new_tweets:
            print(f"[INFO] @{username} の新規ポストはありません（最新記録ID: {last_id} / 取得最新ID: {tweets[-1]['id']}）。", flush=True)

        for tweet in new_tweets:
            print(f"[NEW] @{username} の新規ツイートを検出: {tweet['url']}", flush=True)
            if send_to_discord(tweet):
                state[username] = tweet["id"]
                save_state(state)
                time.sleep(2)  # Discordのレートリミット対策


def main():
    if not DISCORD_WEBHOOK_URL:
        raise ValueError(
            "エラー: 環境変数 DISCORD_WEBHOOK_URL が設定されていません。GitHub Secrets 等を確認してください。"
        )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 監視対象 ({', '.join('@' + u for u in TARGET_USERS)}) の新規ツイートを確認中...", flush=True)
    check_and_notify()
    print("チェック完了。", flush=True)


if __name__ == "__main__":
    main()
