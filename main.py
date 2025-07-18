# # main.py push
# from fastapi import FastAPI, Form
# from pydantic import BaseModel
# from amazon.paapi import AmazonAPI
# import psycopg2
# import re
# from fastapi import HTTPException
# import os
# from dotenv import load_dotenv
# from fastapi.middleware.cors import CORSMiddleware
# from apns2.client import APNsClient
# from apns2.payload import Payload
# from apns2.credentials import TokenCredentials
# from typing import Union
# import traceback
# import time
# import jwt
# import requests


# load_dotenv()


# # 認証情報
# KEY = os.getenv("AMAZON_KEY")
# SECRET = os.getenv("AMAZON_SECRET")
# TAG = os.getenv("AMAZON_TAG")
# COUNTRY = os.getenv("AMAZON_COUNTRY")
# DATABASE_URL = os.getenv("POSTGRES_URL")

# # push通知用認証情報
# TEAM_ID = os.getenv("TEAM_ID")
# KEY_ID = os.getenv("KEY_ID")
# BUNDLE_ID = os.getenv("BUNDLE_ID")
# AUTH_KEY_PATH = os.getenv("AUTH_KEY_PATH")


# # Render上で、かつ KEY_P8 が存在する場合のみファイル生成
# if os.getenv("RENDER") and os.getenv("KEY_P8"):
#     with open("AuthKey_Q7F6VRBP8F.p8", "w") as f:
#         f.write(os.getenv("KEY_P8").replace("\\n", "\n"))
    
#     # with open("AuthKey_J8KCXKK48A.p8", "r") as f:
#     #     print(f.read())  # 実際の内容を確認（複数行になっていればOK）

# app = FastAPI()

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# # 認証トークンの生成
# credentials = TokenCredentials(
#     AUTH_KEY_PATH,
#     TEAM_ID,
#     KEY_ID,
# )

# # APNsクライアントの準備（開発用：use_sandbox=True）
# client = APNsClient(
#     credentials,
#     use_sandbox=True,
#     use_alternative_port=False
# )


# # リクエストボディの型定義
# class NotificationRequest(BaseModel):
#     token: str
#     message: str

# # PostgreSQL 接続（環境変数または設定ファイルで管理するのが望ましい）
# conn = psycopg2.connect(DATABASE_URL)
# cursor = conn.cursor()

# # テーブル作成
# cursor.execute('''
# CREATE TABLE IF NOT EXISTS products (
#     id SERIAL PRIMARY KEY,
#     user_id TEXT,
#     asin TEXT,
#     title TEXT,
#     price REAL,
#     url TEXT,
#     target_price REAL
# );
# ''')
# conn.commit()

# # モデル定義
# class RegisterItem(BaseModel):
#     url: str
#     target_price: float

# class BulkRegisterRequest(BaseModel):
#     user_id: str
#     items: list[RegisterItem]

# class CheckPriceRequest(BaseModel):
#     user_id: str

# # 通知送信エンドポイント
# @app.post("/notify")
# async def send_notification(data: NotificationRequest):
#     try:
#         # 🔐 JWTトークン生成
#         with open(AUTH_KEY_PATH) as f:
#             secret = f.read()

#         now = int(time.time())
#         token = jwt.encode(
#             {
#                 "iss": TEAM_ID,
#                 "iat": now
#             },
#             secret,
#             algorithm="ES256",
#             headers={"alg": "ES256", "kid": KEY_ID}
#         )

#         # 📦 通知Payload
#         payload = {
#             "aps": {
#                 "alert": data.message,
#                 "sound": "default",
#                 "badge": 1
#             }
#         }

#         # APNs エンドポイント（開発環境用）
#         url = f"https://api.sandbox.push.apple.com/3/device/{data.token}"

#         headers = {
#             "authorization": f"bearer {token}",
#             "apns-topic": BUNDLE_ID,
#             "apns-push-type": "alert"
#         }

#         # 通知送信
#         res = requests.post(url, json=payload, headers=headers)

#         if res.status_code == 200:
#             return {"status": "✅ 通知送信成功"}
#         else:
#             print("❌ 通知失敗:", res.text)
#             raise HTTPException(status_code=500, detail=f"APNs Error: {res.text}")

#     except Exception as e:
#         print("通知送信エラー:", e)
#         raise HTTPException(status_code=500, detail=f"❌ 通知送信失敗: {str(e)}")

# # ASIN 抽出関数
# def extract_asin(url: str) -> Union[str, None]:
#     match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
#     return match.group(1) if match else None

# # 商品登録
# @app.post("/bulk_register")
# def bulk_register(req: BulkRegisterRequest):
#     cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = %s", (req.user_id,))
#     current_count = cursor.fetchone()[0]
#     if current_count + len(req.items) > 10:
#         return {"error": f"登録できるのは最大10件までです。現在: {current_count}件"}

#     asin_map = {}
#     for item in req.items:
#         asin = extract_asin(item.url)
#         if not asin:
#             return {"error": f"無効なURLが含まれています: {item.url}"}
#         asin_map[asin] = item

#     asin_list = list(asin_map.keys())
#     amazon = AmazonAPI(KEY, SECRET, TAG, COUNTRY)
#     products = amazon.get_items(item_id_type="ASIN", item_ids=asin_list)

#     if not products["data"]:
#         return {"error": "Amazonから商品情報を取得できませんでした"}

#     results = []
#     for asin in asin_list:
#         try:
#             item = products["data"][asin]
#             title = item.item_info.title.display_value
#             price = int(item.offers.listings[0].price.amount)
#             url = item.detail_page_url
#             target_price = asin_map[asin].target_price

#             cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = %s AND asin = %s", (req.user_id, asin))
#             exists = cursor.fetchone()[0]
#             if exists > 0:
#                 results.append({"asin": asin, "message": "すでに登録済みのためスキップ"})
#                 continue

#             cursor.execute('''
#             INSERT INTO products (user_id, asin, title, price, url, target_price)
#             VALUES (%s, %s, %s, %s, %s, %s)
#             ''', (req.user_id, asin, title, price, url, target_price))
#             results.append({"asin": asin, "title": title, "current_price": price})

#         except Exception as e:
#             results.append({"asin": asin, "error": f"処理中にエラー: {str(e)}"})

#     conn.commit()
#     return {"message": f"{len(results)}件登録完了", "items": results}

# # 商品一覧取得
# @app.get("/products/{user_id}")
# def get_products(user_id: str):
#     cursor.execute("SELECT asin, title, price, url, target_price FROM products WHERE user_id = %s", (user_id,))
#     rows = cursor.fetchall()
#     return {"items": [{"asin": r[0], "title": r[1], "current_price": r[2], "url": r[3], "target_price": r[4]} for r in rows]}

# # 商品削除
# @app.delete("/product")
# def delete_product(user_id: str, asin: str):
#     cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = %s AND asin = %s", (user_id, asin))
#     count = cursor.fetchone()[0]
#     if count == 0:
#         raise HTTPException(status_code=404, detail="該当の商品が見つかりません")
#     cursor.execute("DELETE FROM products WHERE user_id = %s AND asin = %s", (user_id, asin))
#     conn.commit()
#     return {"message": f"ASIN {asin} を削除しました"}

# # 価格チェック
# @app.post("/check_prices")
# def check_prices(req: CheckPriceRequest):
#     cursor.execute("SELECT asin, title, target_price, url FROM products WHERE user_id = %s", (req.user_id,))
#     rows = cursor.fetchall()
#     if not rows:
#         return {"notifications": []}

#     asin_list = [r[0] for r in rows]
#     asin_to_target = {r[0]: {"title": r[1], "target_price": r[2], "url": r[3]} for r in rows}

#     amazon = AmazonAPI(KEY, SECRET, TAG, COUNTRY)
#     products = amazon.get_items(item_id_type="ASIN", item_ids=asin_list)
#     if "data" not in products:
#         return {"error": "Amazon APIから商品情報を取得できませんでした"}

#     notifications = []
#     for asin in asin_list:
#         try:
#             item = products["data"][asin]
#             current_price = item.offers.listings[0].price.amount
#             target_price = asin_to_target[asin]["target_price"]
#             if current_price <= target_price:
#                 notifications.append({
#                     "asin": asin,
#                     "title": asin_to_target[asin]["title"],
#                     "current_price": current_price,
#                     "target_price": target_price,
#                     "url": asin_to_target[asin]["url"]
#                 })
#         except Exception as e:
#             print(f"エラー: {asin}: {e}")

#     return {"notifications": notifications}

from fastapi import FastAPI, Form, HTTPException
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
            if res.status_code == 200:
                return {"status": "✅ 通知送信成功"}
            raise HTTPException(status_code=500, detail=f"APNs Error: {res.text}")

    except Exception as e:
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
