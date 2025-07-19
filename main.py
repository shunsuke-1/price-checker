from fastapi import FastAPI, Form, HTTPException, Depends
from pydantic import BaseModel
from amazon.paapi import AmazonAPI
import psycopg2
import re
import os
import time
import jwt
import httpx
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from typing import Union
import traceback

load_dotenv()

# Amazon 認証情報
KEY = os.getenv("AMAZON_KEY")
SECRET = os.getenv("AMAZON_SECRET")
TAG = os.getenv("AMAZON_TAG")
COUNTRY = os.getenv("AMAZON_COUNTRY")
DATABASE_URL = os.getenv("POSTGRES_URL")

# push通知用認証情報
TEAM_ID = os.getenv("TEAM_ID")
KEY_ID = os.getenv("KEY_ID")
BUNDLE_ID = os.getenv("BUNDLE_ID")
AUTH_KEY_PATH = os.getenv("AUTH_KEY_PATH")

# Render上で、かつ KEY_P8 が存在する場合のみファイル生成
if os.getenv("RENDER") and os.getenv("KEY_P8"):
    with open(AUTH_KEY_PATH, "w") as f:
        f.write(os.getenv("KEY_P8").replace("\\n", "\n"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class NotificationRequest(BaseModel):
    token: str
    message: str

class RegisterItem(BaseModel):
    url: str
    target_price: float

class BulkRegisterRequest(BaseModel):
    user_id: str
    items: list[RegisterItem]

class CheckPriceRequest(BaseModel):
    user_id: str

class DeviceTokenRequest(BaseModel):
    user_id: str
    token: str

# DB接続
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    user_id TEXT,
    asin TEXT,
    title TEXT,
    price REAL,
    url TEXT,
    target_price REAL
);
''')
conn.commit()

# Create Table for store Device Token
cursor.execute('''
CREATE TABLE IF NOT EXISTS device_tokens (
    user_id TEXT PRIMARY KEY,
    token TEXT
);
''')
conn.commit()

@app.post("/run_check")
async def run_check():
    await check_and_notify()
    return {"message": "価格チェック完了"}


async def check_and_notify():
    cursor.execute("""
        SELECT user_id, asin, title, target_price, url FROM products
    """)
    rows = cursor.fetchall()

    if not rows:
        return

    asin_set = {r[1] for r in rows}
    amazon = AmazonAPI(KEY, SECRET, TAG, COUNTRY)
    products = amazon.get_items(item_id_type="ASIN", item_ids=list(asin_set))

    user_notifications = {}

    for row in rows:
        user_id, asin, title, target_price, url = row
        try:
            item = products["data"][asin]
            current_price = item.offers.listings[0].price.amount
            if current_price <= target_price:
                if user_id not in user_notifications:
                    user_notifications[user_id] = []
                user_notifications[user_id].append(f"🛒 {title} が買い時です！（{int(current_price)}円）")
        except Exception as e:
            print(f"価格取得失敗: {asin}, エラー: {e}")

    for user_id, messages in user_notifications.items():
        cursor.execute("SELECT token FROM device_tokens WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if row:
            token = row[0]
            message = "\n".join(messages)
            await send_notification(NotificationRequest(token=token, message=message))



@app.post("/register_token")
def register_token(req: DeviceTokenRequest):
    cursor.execute("INSERT INTO device_tokens (user_id, token) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET token = EXCLUDED.token", (req.user_id, req.token))
    conn.commit()
    return {"message": "✅ トークンを登録しました"}


# ユーザーIDから取得したトークンを使って通知
@app.post("/notify_user")
async def notify_user(user_id: str, message: str):
    cursor.execute("SELECT token FROM device_tokens WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="トークンが登録されていません")
    
    token = row[0]
    return await send_notification(NotificationRequest(token=token, message=message))


@app.post("/notify")
async def send_notification(data: NotificationRequest):
    try:
        with open(AUTH_KEY_PATH) as f:
            secret = f.read()

        now = int(time.time())
        token = jwt.encode({"iss": TEAM_ID, "iat": now}, secret, algorithm="ES256", headers={"alg": "ES256", "kid": KEY_ID})

        headers = {
            "authorization": f"bearer {token}",
            "apns-topic": BUNDLE_ID,
            "apns-push-type": "alert"
        }

        payload = {
            "aps": {
                "alert": data.message,
                "sound": "default",
                "badge": 1
            }
        }

        async with httpx.AsyncClient(http2=True) as client:
            res = await client.post(f"https://api.sandbox.push.apple.com/3/device/{data.token}", json=payload, headers=headers)

            print(f"🔁 Status Code: {res.status_code}")
            print(f"📨 Response: {res.text}")
            print(f"📨 Headers: {res.headers}")

            if res.status_code == 200:
                return {"status": "✅ 通知送信成功"}
            raise HTTPException(status_code=500, detail=f"APNs Error: {res.text}")

    except Exception as e:
        print("通知送信エラー:", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"❌ 通知送信失敗: {str(e)}")

def extract_asin(url: str) -> Union[str, None]:
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
    return match.group(1) if match else None

@app.post("/bulk_register")
def bulk_register(req: BulkRegisterRequest):
    cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = %s", (req.user_id,))
    current_count = cursor.fetchone()[0]
    if current_count + len(req.items) > 10:
        return {"error": f"登録できるのは最大10件までです。現在: {current_count}件"}

    asin_map = {extract_asin(item.url): item for item in req.items if extract_asin(item.url)}
    asin_list = list(asin_map.keys())
    amazon = AmazonAPI(KEY, SECRET, TAG, COUNTRY)
    products = amazon.get_items(item_id_type="ASIN", item_ids=asin_list)
    if not products["data"]:
        return {"error": "Amazonから商品情報を取得できませんでした"}

    results = []
    for asin in asin_list:
        try:
            item = products["data"][asin]
            title = item.item_info.title.display_value
            price = int(item.offers.listings[0].price.amount)
            url = item.detail_page_url
            target_price = asin_map[asin].target_price

            cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = %s AND asin = %s", (req.user_id, asin))
            if cursor.fetchone()[0] > 0:
                results.append({"asin": asin, "message": "すでに登録済みのためスキップ"})
                continue

            cursor.execute('''
                INSERT INTO products (user_id, asin, title, price, url, target_price)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (req.user_id, asin, title, price, url, target_price))
            results.append({"asin": asin, "title": title, "current_price": price})

        except Exception as e:
            results.append({"asin": asin, "error": f"処理中にエラー: {str(e)}"})

    conn.commit()
    return {"message": f"{len(results)}件登録完了", "items": results}

@app.get("/products/{user_id}")
def get_products(user_id: str):
    cursor.execute("SELECT asin, title, price, url, target_price FROM products WHERE user_id = %s", (user_id,))
    rows = cursor.fetchall()
    return {"items": [{"asin": r[0], "title": r[1], "current_price": r[2], "url": r[3], "target_price": r[4]} for r in rows]}

@app.delete("/product")
def delete_product(user_id: str, asin: str):
    cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = %s AND asin = %s", (user_id, asin))
    if cursor.fetchone()[0] == 0:
        raise HTTPException(status_code=404, detail="該当の商品が見つかりません")
    cursor.execute("DELETE FROM products WHERE user_id = %s AND asin = %s", (user_id, asin))
    conn.commit()
    return {"message": f"ASIN {asin} を削除しました"}

@app.post("/check_prices")
def check_prices(req: CheckPriceRequest):
    cursor.execute("SELECT asin, title, target_price, url FROM products WHERE user_id = %s", (req.user_id,))
    rows = cursor.fetchall()
    if not rows:
        return {"notifications": []}

    asin_list = [r[0] for r in rows]
    asin_to_target = {r[0]: {"title": r[1], "target_price": r[2], "url": r[3]} for r in rows}
    amazon = AmazonAPI(KEY, SECRET, TAG, COUNTRY)
    products = amazon.get_items(item_id_type="ASIN", item_ids=asin_list)

    notifications = []
    for asin in asin_list:
        try:
            item = products["data"][asin]
            current_price = item.offers.listings[0].price.amount
            target_price = asin_to_target[asin]["target_price"]
            if current_price <= target_price:
                notifications.append({
                    "asin": asin,
                    "title": asin_to_target[asin]["title"],
                    "current_price": current_price,
                    "target_price": target_price,
                    "url": asin_to_target[asin]["url"]
                })
        except Exception as e:
            print(f"エラー: {asin}: {e}")

    return {"notifications": notifications}
