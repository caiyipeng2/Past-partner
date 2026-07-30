#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import http.server
import socketserver
import json
import os
import urllib.parse
import base64
import tempfile
from datetime import datetime
import cgi

# 添加项目根目录到Python路径
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# 定义端口
PORT = 8080

# 创建数据目录
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'uploaded')
os.makedirs(DATA_DIR, exist_ok=True)

# 文档目录
DOCS_DIR = os.path.join(os.path.dirname(__file__), '..', 'docs')

class ChatHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        print(f"收到POST请求: {self.path}")  # 调试信息
        if self.path == '/api/upload-chat-data':
            self.handle_upload_chat_data()
        elif self.path == '/api/upload-db-file':
            self.handle_upload_db_file()
        else:
            print(f"未找到API端点: {self.path}")  # 调试信息
            self.send_error(404, "API endpoint not found")

    def do_GET(self):
        print(f"收到GET请求: {self.path}")  # 调试信息
        # 处理文档请求
        if self.path.startswith('/docs/'):
            self.handle_docs_request()
        elif self.path.startswith('/api/'):
            if self.path == '/api/health':
                self.handle_health_check()
            else:
                print(f"未找到API端点: {self.path}")  # 调试信息
                self.send_error(404, "API endpoint not found")
        else:
            # 处理静态文件
            self.handle_static_file()

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
            '.md': 'text/markdown',
        }
        
        content_type = mime_types.get(ext, 'application/octet-stream')
        
        # 读取文件
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            
            self.send_response(200)
            self.send_header('Content-type', content_type)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, "File not found")
        except Exception as e:
            self.send_error(500, f"Internal server error: {str(e)}")

    def handle_docs_request(self):
        """处理文档请求"""
        try:
            # 获取文档路径
            doc_path = self.path[len('/docs/'):]
            file_path = os.path.join(DOCS_DIR, doc_path)
            
            # 检查文件是否存在
            if not os.path.exists(file_path):
                self.send_error(404, "Document not found")
                return
            
            # 读取文件
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 设置响应头
            self.send_response(200)
            self.send_header('Content-type', 'text/markdown; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            # 发送内容
            self.wfile.write(content.encode('utf-8'))
            
        except Exception as e:
            print(f"处理文档请求失败: {e}")
            self.send_error(500, "Internal server error")

    def handle_upload_db_file(self):
        """处理数据库文件上传"""
        print("处理数据库文件上传请求")  # 调试信息
        try:
            # 获取内容长度
            content_length = int(self.headers['Content-Length'])
            print(f"内容长度: {content_length}")  # 调试信息
            
            # 读取原始数据
            raw_data = self.rfile.read(content_length)
            print(f"接收到数据长度: {len(raw_data)}")  # 调试信息
            
            # 生成唯一文件名
            user_id = "default_user"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"wechat_db_{user_id}_{timestamp}.db"
            file_path = os.path.join(DATA_DIR, filename)
            print(f"保存文件路径: {file_path}")  # 调试信息
            
            # 保存文件
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
            print(f"返回响应: {response}")  # 调试信息
            
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
            database_contents = []
            
            for item in chat_data:
                if isinstance(item, dict) and item.get('type') == 'database':
                    database_files.append(item)
                elif isinstance(item, dict) and 'isDatabaseContent' in item:
                    database_contents.append(item)
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
            
            # 解析数据库内容（如果有解析器的话）
            parsed_db_messages = []
            # 尝试导入微信解析器
            try:
                from utils.wechat_parser import WeChatDBParser
                if database_contents:
                    parser = WeChatDBParser()
                    for db_content in database_contents:
                        try:
                            # 这里应该解析实际的数据库内容
                            # 由于前端无法直接上传二进制文件，我们需要特殊的处理
                            # 在实际应用中，这里会解析数据库文件内容
                            pass
                        except Exception as e:
                            print(f"解析数据库内容失败: {e}")
            except ImportError:
                print("警告: 无法导入微信解析器，数据库文件解析功能将不可用")
            
            # 计算总数
            total_regular = regular_count + len(parsed_db_messages)
            total_databases = db_info_count
            
            # 返回成功响应
            total_count = total_regular + total_databases
            response = {
                'success': True,
                'message_count': total_count,
                'regular_messages': total_regular,
                'database_files': total_databases,
                'message': f'成功处理 {total_count} 项数据 ({total_regular} 条消息, {total_databases} 个数据库相关信息)'
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

# 设置服务器
print(f"服务器启动在 http://localhost:{PORT}")
# 检查微信解析器是否可用
try:
    from utils.wechat_parser import WeChatDBParser
    print("微信解析器状态: 可用")
except ImportError:
    print("微信解析器状态: 不可用")

print("按 Ctrl+C 停止服务器")

if __name__ == "__main__":
    try:
        with socketserver.TCPServer(("", PORT), ChatHTTPRequestHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")