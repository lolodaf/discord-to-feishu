import requests
import json
import os
import time
import threading
import io
from datetime import datetime, timezone, timedelta
from flask import Flask

# --- 配置加载 ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")
# 支持多群组配置：CHANNEL_ID1 对应 FEISHU_RECEIVE_ID1
history = {}

def load_config():
    config_list = []
    # 默认组
    ch_env = os.getenv("CHANNEL_ID")
    receive_id = os.getenv("FEISHU_RECEIVE_ID")
    if ch_env and receive_id:
        config_list.append({"channels": ch_env.split(","), "receive_id": receive_id})
    # 多组支持
    for i in range(1, 6):
        ch = os.getenv(f"CHANNEL_ID{i}")
        rid = os.getenv(f"FEISHU_RECEIVE_ID{i}")
        if ch and rid:
            config_list.append({"channels": ch.split(","), "receive_id": rid})
    return config_list

CONFIG_LIST = load_config()

# --- 飞书 API 核心类 ---
class FeishuBot:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = ""
        self.expire_time = 0

    def get_token(self):
        """获取飞书身份凭证"""
        if time.time() < self.expire_time:
            return self.token
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        res = requests.post(url, json={"app_id": self.app_id, "app_secret": self.app_secret}).json()
        self.token = res.get("tenant_access_token", "")
        self.expire_time = time.time() + res.get("expire", 3600) - 60
        return self.token

    def upload_image(self, img_url):
        """下载 Discord 图片并上传到飞书，换取 image_key"""
        try:
            # 1. 下载图片
            img_res = requests.get(img_url, timeout=15)
            if img_res.status_code != 200: return None
            
            # 2. 上传到飞书
            url = "https://open.feishu.cn/open-apis/im/v1/images"
            headers = {"Authorization": f"Bearer {self.get_token()}"}
            files = {
                "image_type": (None, "message"),
                "image": ("discord_img.png", io.BytesIO(img_res.content), "image/png")
            }
            res = requests.post(url, headers=headers, files=files).json()
            return res.get("data", {}).get("image_key")
        except:
            return None

    def send_card(self, receive_id, title, content, image_key=None):
        """发送带图片的卡片消息"""
        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {self.get_token()}", "Content-Type": "application/json"}
        
        elements = [{"tag": "markdown", "content": content}]
        # 如果有图片，在卡片里插入图片模块
        if image_key:
            elements.insert(0, {"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": "图片"}})

        card = {
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
            "elements": elements
        }
        payload = {"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card)}
        requests.post(url, headers=headers, json=payload)

bot = FeishuBot(APP_ID, APP_SECRET)

# --- Discord 逻辑 (保持不变，仅修改发送部分) ---
def get_recent_messages(channel_id):
    headers = {"Authorization": DISCORD_TOKEN}
    res = requests.get(f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=5", headers=headers)
    return res.json() if res.status_code == 200 else []

def background_monitor():
    while True:
        for group in CONFIG_LIST:
            for ch_id in group["channels"]:
                messages = get_recent_messages(ch_id.strip())
                if not messages: continue
                
                last_id = history.get(ch_id)
                if last_id and messages[0]['id'] != last_id:
                    new_msg = messages[0] # 简化逻辑：仅转发最新一条
                    author = new_msg.get('author', {}).get('username', '未知')
                    text = new_msg.get('content', '')
                    
                    # 尝试抓取第一张图片
                    img_key = None
                    attachments = new_msg.get('attachments', [])
                    if attachments:
                        img_key = bot.upload_image(attachments[0]['url'])
                    
                    md = f"**用户**: {author}\n**内容**: {text}"
                    bot.send_card(group["receive_id"], "Discord 新动态", md, img_key)
                
                history[ch_id] = messages[0]['id']
        time.sleep(60)

# --- Web 服务器 ---
app = Flask(__name__)
@app.route('/')
def home(): 
    return f"Bot Running. Configured Groups: {len(CONFIG_LIST)}<br>Tip: Use Bot API to get Chat ID."

if __name__ == '__main__':
    threading.Thread(target=background_monitor, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
