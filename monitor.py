import requests
import json
import os
import time
import threading
import io
from datetime import datetime, timezone, timedelta
from flask import Flask

# --- 1. 配置与初始化 ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")
history = {}
CHANNEL_NAMES_CACHE = {} 

def load_config():
    """读取多组配置：CHANNEL_ID1 -> FEISHU_RECEIVE_ID1 ..."""
    config_list = []
    for i in range(1, 11):
        ch = os.getenv(f"CHANNEL_ID{i}")
        rid = os.getenv(f"FEISHU_RECEIVE_ID{i}")
        if ch and rid:
            clean_channels = [c.strip() for c in ch.split(",") if c.strip()]
            config_list.append({"channels": clean_channels, "receive_id": rid.strip()})
    return config_list

CONFIG_LIST = load_config()

# --- 2. 核心辅助工具 ---
def get_channel_name(channel_id):
    """获取 Discord 频道名称并缓存"""
    if channel_id in CHANNEL_NAMES_CACHE: return CHANNEL_NAMES_CACHE[channel_id] 
    url = f"https://discord.com/api/v9/channels/{channel_id}"
    headers = {"Authorization": DISCORD_TOKEN}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            name = res.json().get('name', channel_id)
            CHANNEL_NAMES_CACHE[channel_id] = name 
            return name
    except: pass
    return channel_id 

def format_discord_time(raw_time_str):
    """将 Discord 时间转为北京时间格式"""
    if not raw_time_str: return "未知时间"
    try:
        dt_utc = datetime.fromisoformat(raw_time_str.replace('Z', '+00:00'))
        return dt_utc.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    except: return raw_time_str

# --- 3. 飞书 API 类 ---
class FeishuBot:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = ""
        self.expire_time = 0

    def get_token(self):
        if time.time() < self.expire_time: return self.token
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        res = requests.post(url, json={"app_id": self.app_id, "app_secret": self.app_secret}).json()
        self.token = res.get("tenant_access_token", "")
        self.expire_time = time.time() + res.get("expire", 3600) - 60
        return self.token

    def upload_image(self, img_url):
        try:
            img_res = requests.get(img_url, timeout=15)
            if img_res.status_code != 200: return None
            url = "https://open.feishu.cn/open-apis/im/v1/images"
            headers = {"Authorization": f"Bearer {self.get_token()}"}
            files = {
                "image_type": (None, "message"),
                "image": ("img.png", io.BytesIO(img_res.content), "image/png")
            }
            res = requests.post(url, headers=headers, files=files).json()
            return res.get("data", {}).get("image_key")
        except: return None

    def send_card(self, receive_id, title, content, image_key=None):
        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {self.get_token()}", "Content-Type": "application/json"}
        elements = [{"tag": "markdown", "content": content}]
        if image_key:
            elements.insert(0, {"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": "img"}})
        card = {
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
            "elements": elements
        }
        requests.post(url, headers=headers, json={"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card)})

bot = FeishuBot(APP_ID, APP_SECRET)

# --- 4. 监控主循环 ---
def background_monitor():
    print(f"🚀 格式优化版监控启动！")
    while True:
        for group in CONFIG_LIST:
            target_id = group["receive_id"]
            for ch_id in group["channels"]:
                try:
                    res = requests.get(f"https://discord.com/api/v9/channels/{ch_id}/messages?limit=10", headers={"Authorization": DISCORD_TOKEN}, timeout=10)
                    messages = res.json() if res.status_code == 200 else []
                except: continue
                
                if not messages: continue
                last_id = history.get(ch_id)
                if last_id:
                    new_msgs = [m for m in messages if m['id'] > last_id]
                    for msg in reversed(new_msgs):
                        # 获取数据
                        channel_name = get_channel_name(ch_id)
                        formatted_time = format_discord_time(msg.get('timestamp', ''))
                        author = msg.get('author', {}).get('username', '未知')
                        content = msg.get('content', '') or "[多媒体内容]"
                        
                        # 图片处理
                        img_key = None
                        atts = msg.get('attachments', [])
                        if atts and any(atts[0]['url'].lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
                            img_key = bot.upload_image(atts[0]['url'])
                        
                        # 🎯 按照你要求的格式排版
                        md = (
                            f"频道: {channel_name}\n"
                            f"时间: {formatted_time}\n"
                            f"用户: {author}\n\n"
                            f"内容: {content}"
                        )
                        
                        bot.send_card(target_id, f"新消息: {channel_name}", md, img_key)
                        time.sleep(1)
                
                history[ch_id] = messages[0]['id']
        time.sleep(60)

# --- 5. Flask 入口 ---
app = Flask(__name__)
@app.route('/')
def home(): return f"Bot Running! Configured Groups: {len(CONFIG_LIST)}"

if __name__ == '__main__':
    threading.Thread(target=background_monitor, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
