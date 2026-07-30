#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import http.server
import socketserver
import json
import os
import urllib.parse
from datetime import datetime

# 添加项目根目录到Python路径
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# 定义端口
PORT = 8000

# 创建数据目录
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'uploaded')
os.makedirs(DATA_DIR, exist_ok=True)

class AdvancedChatHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/api/upload-chat-data':
            self.handle_upload_chat_data()
        elif self.path == '/api/upload-db-file':
            self.handle_upload_db_file()
        else:
            self.send_error(404, "API endpoint not found")

    def do_GET(self):
        if self.path.startswith('/api/'):
            if self.path == '/api/health':
                self.handle_health_check()
            else:
                self.send_error(404, "API endpoint not found")
        else:
            # 处理静态文件
            self.handle_static_file()

    def handle_upload_chat_data(self):
        try:
            # 读取请求体
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            # 解析JSON数据
            request_data = json.loads(post_data.decode('utf-8'))
            chat_data = request_data.get('data', [])
            user_id = request_data.get('user_id', 'default_user')
            
            # 分离不同类型的数据
            regular_messages = []
            database_files = []
            
            for item in chat_data:
                if isinstance(item, dict) and item.get('type') == 'database':
                    database_files.append(item)
                else:
                    regular_messages.append(item)
            
            # 生成唯一文件名前缀
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # 保存普通聊天数据
            regular_count = 0
            if regular_messages:
                filename = f"chat_data_{user_id}_{timestamp}.json"
                file_path = os.path.join(DATA_DIR, filename)
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(regular_messages, f, ensure_ascii=False, indent=2)
                
                regular_count = len(regular_messages)
                print(f"聊天数据已保存: {file_path}, 共 {regular_count} 条消息")
            
            # 处理数据库文件信息
            db_info_count = 0
            if database_files:
                db_filename = f"database_files_{user_id}_{timestamp}.json"
                db_file_path = os.path.join(DATA_DIR, db_filename)
                
                with open(db_file_path, 'w', encoding='utf-8') as f:
                    json.dump(database_files, f, ensure_ascii=False, indent=2)
                
                db_info_count = len(database_files)
                print(f"数据库文件信息已保存: {db_file_path}, 共 {db_info_count} 个文件")
            
            # 返回成功响应
            total_count = regular_count + db_info_count
            response = {
                'success': True,
                'message_count': total_count,
                'regular_messages': regular_count,
                'database_files': db_info_count,
                'message': f'成功处理 {total_count} 项数据 ({regular_count} 条消息, {db_info_count} 个数据库文件信息)'
            }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
            
        except Exception as e:
            print(f"处理上传数据失败: {e}")
            response = {
                'success': False,
                'message_count': 0,
                'message': f'上传失败: {str(e)}'
            }
            
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))

    def handle_upload_db_file(self):
        """处理数据库文件上传"""
        try:
            # 获取内容长度
            content_length = int(self.headers['Content-Length'])
            
            # 读取原始数据
            raw_data = self.rfile.read(content_length)
            
            # 简单解析 multipart 数据 (实际应用中应该使用更完善的库)
            # 这里只是一个简化的实现
            user_id = "default_user"
            
            # 生成唯一文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"wechat_db_{user_id}_{timestamp}.db"
            file_path = os.path.join(DATA_DIR, filename)
            
            # 保存文件（简化处理，实际应该是解析multipart数据）
            with open(file_path, 'wb') as f:
                f.write(raw_data)
            
            # 尝试解析数据库文件
            parsed_messages = 0
            try:
                from utils.wechat_parser import WeChatDBParser
                parser = WeChatDBParser()
                messages = parser.parse_wechat_db(file_path)
                
                if messages:
                    # 保存解析后的消息
                    parsed_filename = f"parsed_wechat_{user_id}_{timestamp}.json"
                    parsed_file_path = os.path.join(DATA_DIR, parsed_filename)
                    
                    with open(parsed_file_path, 'w', encoding='utf-8') as f:
                        json.dump(messages, f, ensure_ascii=False, indent=2)
                    
                    parsed_messages = len(messages)
                    print(f"解析并保存了 {parsed_messages} 条消息到: {parsed_file_path}")
            except ImportError:
                print("警告: 无法导入微信解析器，跳过数据库解析")
            except Exception as e:
                print(f"解析数据库文件失败: {e}")
            
            # 返回成功响应
            response = {
                'success': True,
                'filename': filename,
                'size': os.path.getsize(file_path),
                'parsed_messages': parsed_messages,
                'message': f'数据库文件上传成功，大小 {os.path.getsize(file_path)} 字节'
            }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
            
        except Exception as e:
            print(f"处理数据库文件上传失败: {e}")
            response = {
                'success': False,
                'message': f'上传失败: {str(e)}'
            }
            
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))

    def handle_health_check(self):
        # 检查微信解析器是否可用
        wechat_parser_available = False
        try:
            from utils.wechat_parser import WeChatDBParser
            wechat_parser_available = True
        except ImportError:
            pass
        
        response = {
            'status': 'healthy',
            'service': 'personalized-ai-chat-api',
            'timestamp': datetime.now().isoformat(),
            'wechat_parser_available': wechat_parser_available
        }
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))

    def handle_static_file(self):
        """处理静态文件请求"""
        # 默认静态文件路径
        root = os.path.join(os.path.dirname(__file__))
        path = urllib.parse.unquote(self.path)
        path = path.split('?', 1)[0]
        path = path.split('#', 1)[0]
        
        if path == '/':
            path = '/index.html'
        
        file_path = os.path.join(root, path.lstrip('/'))
        
        # 获取文件扩展名
        ext = os.path.splitext(file_path)[1].lower()
        mime_types = {
            '.html': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.json': 'application/json',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.db': 'application/x-sqlite3',
        }
        
        content_type = mime_types.get(ext, 'application/octet-stream')
        
        # 读取文件
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            
            self.send_response(200)
            self.send_header('Content-type', content_type)
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, "File not found")
        except Exception as e:
            self.send_error(500, f"Internal server error: {str(e)}")

# 设置服务器
print(f"高级服务器启动在 http://localhost:{PORT}")
# 检查微信解析器是否可用
try:
    from utils.wechat_parser import WeChatDBParser
    print("微信解析器状态: 可用")
except ImportError:
    print("微信解析器状态: 不可用")

print("按 Ctrl+C 停止服务器")

if __name__ == "__main__":
    try:
        with socketserver.TCPServer(("", PORT), AdvancedChatHTTPRequestHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")