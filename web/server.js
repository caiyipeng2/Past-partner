// 简单的Web服务器，用于托管前端界面
// Compatibility launcher only: the Python service owns every API and asset.
const childProcess = require('child_process');
if (require.main === module) {
    const result = childProcess.spawnSync(
        process.env.PYTHON || 'python',
        ['-m', 'src.server', ...process.argv.slice(2)],
        {cwd: require('path').resolve(__dirname, '..'), stdio: 'inherit'}
    );
    process.exit(result.status === null ? 1 : result.status);
}

const http = require('http');
const fs = require('fs');
const path = require('path');

// MIME类型映射
const mimeTypes = {
    '.html': 'text/html',
    '.css': 'text/css',
    '.js': 'text/javascript',
    '.json': 'application/json',
    '.png': 'image/png',
    '.jpg': 'image/jpg',
    '.gif': 'image/gif'
};

// 创建HTTP服务器
const server = http.createServer((req, res) => {
    console.log(`请求: ${req.method} ${req.url}`);
    
    // 处理API请求
    if (req.url.startsWith('/api/')) {
        handleApiRequest(req, res);
        return;
    }
    
    // 处理静态文件
    handleStaticFileRequest(req, res);
});

// 处理API请求
function handleApiRequest(req, res) {
    if (req.url === '/api/upload-chat-data' && req.method === 'POST') {
        handleUploadChatData(req, res);
    } else if (req.url === '/api/health' && req.method === 'GET') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'healthy', service: 'personalized-ai-chat-api' }));
    } else {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'API endpoint not found' }));
    }
}

// 处理聊天数据上传
function handleUploadChatData(req, res) {
    let body = '';
    
    req.on('data', chunk => {
        body += chunk.toString();
    });
    
    req.on('end', () => {
        try {
            const requestData = JSON.parse(body);
            const chatData = requestData.data || [];
            const userId = requestData.user_id || 'default_user';
            
            // 确保数据目录存在
            const dataDir = path.join(__dirname, '..', 'data', 'uploaded');
            if (!fs.existsSync(dataDir)) {
                fs.mkdirSync(dataDir, { recursive: true });
            }
            
            // 生成唯一文件名
            const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
            const filename = `chat_data_${userId}_${timestamp}.json`;
            const filePath = path.join(dataDir, filename);
            
            // 保存聊天数据
            fs.writeFileSync(filePath, JSON.stringify(chatData, null, 2));
            
            // 返回成功响应
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
                success: true,
                message_count: chatData.length,
                message: `成功上传 ${chatData.length} 条聊天记录`,
                file_path: filePath
            }));
            
            console.log(`聊天数据已保存: ${filePath}, 共 ${chatData.length} 条消息`);
        } catch (error) {
            console.error('处理上传数据失败:', error);
            res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
                success: false,
                message_count: 0,
                message: `上传失败: ${error.message}`
            }));
        }
    });
}

// 处理静态文件请求
function handleStaticFileRequest(req, res) {
    // 设置默认页面
    let filePath = req.url === '/' ? '/index.html' : req.url;
    filePath = path.join(__dirname, filePath);
    
    // 获取文件扩展名
    const extname = String(path.extname(filePath)).toLowerCase();
    const contentType = mimeTypes[extname] || 'application/octet-stream';
    
    // 读取文件
    fs.readFile(filePath, (error, content) => {
        if (error) {
            if (error.code === 'ENOENT') {
                // 文件未找到
                console.log(`文件未找到: ${filePath}`);
                res.writeHead(404);
                res.end('404 Not Found');
            } else {
                // 其他服务器错误
                console.log(`服务器错误: ${error.code}`);
                res.writeHead(500);
                res.end('500 Internal Server Error');
            }
        } else {
            // 成功读取文件
            res.writeHead(200, { 'Content-Type': contentType });
            res.end(content, 'utf-8');
        }
    });
}

// 启动服务器
const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`服务器运行在 http://localhost:${PORT}`);
    console.log('按 Ctrl+C 停止服务器');
});
