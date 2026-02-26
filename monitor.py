import requests
import json
import os
import time
import threading
import io
from datetime import datetime, timezone, timedelta
from flask import Flask

# --- 1. 配置加载 ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")
history = {}

def load_config():
    """根据你的截图逻辑，匹配 CHANNEL_IDx 和 FEISHU_RECEIVE_IDx"""
    config_list = []
    # 循环读取 1-10 组配置
    for i in range(1, 11):
        ch = os.getenv(f"CHANNEL_ID{i}")
        # 注意：这里改读 RECEIVE_ID，用来对应不同的群
        rid = os.getenv(f"FEISHU_RECEIVE_ID{i}")
        if ch and rid:
            clean_channels = [c.strip() for c in ch.split(",") if c.strip()]
            config_list.append({"channels": clean_channels, "receive_id": rid.strip()})
    return config_list

CONFIG_LIST = load_config()

# --- 2. 飞书高级 API 类 ---
class FeishuAdvancedBot:
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
        """核心：下载 Discord 图片并上传到飞书，换取能够显示的 image_key"""
        try:
            img_res = requests.get(img_url, timeout=15)
            if img_res.status_code != 200: return None
            url = "https://open.feishu.cn/open-apis/im/v1/images"
            headers = {"Authorization": f"Bearer {self.get_token()}"}
            files = {
                "image_type": (None, "message"),
                "image": ("discord_img.png", io.BytesIO(img_res.content), "image/png")
            }
            res = requests.post(url, headers=headers, files=files).json()
            return res.get("data", {}).get("image_key")
        except: return None

    def send_card(self, receive_id, title, content, image_key=None):
        """发送带缩略图的交互式卡片"""
        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {self.get_token()}", "Content-Type": "application/json"}
        
        elements = [{"tag": "markdown", "content": content}]
        # 如果有图片，将图片模块插入到卡片最上方
        if image_key:
            elements.insert(0, {"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": "图片"}})

        card = {
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
            "elements": elements
        }
        payload = {"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card)}
        requests.post(url, headers=headers, json=payload)

feishu_bot = FeishuAdvancedBot(APP_ID, APP_SECRET)

# --- 3. 监控与转发逻辑 ---
def get_recent_messages(channel_id):
    headers = {"Authorization": DISCORD_TOKEN}
    try:
        res = requests.get(f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=10", headers=headers, timeout=10)
        return res.json() if res.status_code == 200 else []
    except: return []

def background_monitor():
    print(f"🚀 高级缩略图版监控启动！加载了 {len(CONFIG_LIST)} 组对应关系。")
    while True:
        for group in CONFIG_LIST:
            target_chat_id = group["receive_id"]
            for ch_id in group["channels"]:
                messages = get_recent_messages(ch_id)
                if not messages: continue
                
                last_id = history.get(ch_id)
                if last_id:
                    new_msgs = [m for m in messages if m['id'] > last_id]
                    for msg in reversed(new_msgs):
                        author = msg.get('author', {}).get('username', '未知')
                        content = msg.get('content', '') or "[多媒体消息]"
                        
                        # 处理图片缩略图
                        img_key = None
                        attachments = msg.get('attachments', [])
                        if attachments and any(attachments[0]['url'].lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
                            print(f"正在处理图片上传: {attachments[0]['url']}")
                            img_key = feishu_bot.upload_image(attachments[0]['url'])
                        
                        md_text = f"**发送者**: {author}\n**内容**: {content}"
                        feishu_bot.send_card(target_chat_id, "Discord 实时监控", md_text, img_key)
                        time.sleep(1) # 避开频率限制
                
                history[ch_id] = messages[0]['id']
        time.sleep(60)

# --- 4. 服务入口 ---
app = Flask(__name__)
@app.route('/')
def home(): return "Advanced Feishu Bot is running! ✅"

if __name__ == '__main__':
    threading.Thread(target=background_monitor, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
