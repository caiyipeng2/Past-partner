// 聊天应用主逻辑

// DOM元素引用
const chatHistory = document.getElementById('chatHistory');
const messageInput = document.getElementById('messageInput');
const sendButton = document.getElementById('sendButton');
const personalitySelect = document.getElementById('personalitySelect');
const chatFileInput = document.getElementById('chatFile');
const chatFolderInput = document.getElementById('chatFolder');
const fileInfoSpan = document.getElementById('fileInfo');
const clearSelectionButton = document.getElementById('clearSelection');
const uploadButton = document.getElementById('uploadButton');

// 存储选中的文件
let selectedFiles = [];

// 添加消息到聊天历史
function addMessage(content, isUser = false) {
    const messageDiv = document.createElement('div');
    messageDiv.className = isUser ? 'message user-message' : 'message ai-message';
    
    const now = new Date();
    const timestamp = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;
    
    messageDiv.innerHTML = `
        <div class="message-content">${content}</div>
        <div class="timestamp">${timestamp}</div>
    `;
    
    chatHistory.appendChild(messageDiv);
    
    // 滚动到底部
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// 发送消息
async function sendMessage() {
    const content = messageInput.value.trim();
    if (!content) return;
    
    // 添加用户消息
    addMessage(content, true);
    
    // 清空输入框
    messageInput.value = '';
    
    // 禁用发送按钮防止重复点击
    sendButton.disabled = true;
    sendButton.textContent = '发送中...';
    
    try {
        // 这里应该调用实际的API
        // 模拟API调用延迟
        await new Promise(resolve => setTimeout(resolve, 1000));
        
        // 模拟AI回复（在实际应用中这里会调用后端API）
        const personality = personalitySelect.value;
        const response = generateMockResponse(content, personality);
        
        // 添加AI回复
        addMessage(response);
    } catch (error) {
        console.error('发送消息失败:', error);
        addMessage('抱歉，消息发送失败，请稍后重试。', false);
    } finally {
        // 重新启用发送按钮
        sendButton.disabled = false;
        sendButton.textContent = '发送';
    }
}

// 生成模拟回复（在实际应用中会被真实API替换）
function generateMockResponse(userMessage, personality) {
    const responses = {
        default: [
            "我理解你的想法，这确实是个有趣的观点。",
            "谢谢你分享这些，我很乐意继续这个话题。",
            "关于你说的这件事，我也有一些类似的经历。",
            "这是个很好的问题，让我想想如何回答。",
            "我明白你的感受，这种情况下确实不容易做决定。"
        ],
        friend: [
            "哈哈，你总是这么有趣！",
            "哎呀，我也有过类似的经历呢！",
            "你真的太棒了，我觉得你一定能行的！",
            "别担心啦，一切都会好起来的~",
            "咱们什么时候一起出去玩啊？"
        ],
        partner: [
            "亲爱的，你的想法对我很重要。",
            "无论发生什么，我都会陪在你身边。",
            "你的感受我完全理解，这确实不容易。",
            "我相信你能做出最好的决定。",
            "谢谢你愿意和我分享这些心里话。"
        ],
        professional: [
            "根据我的分析，您提到的情况有以下几点值得考虑...",
            "感谢您的咨询，我建议从以下几个维度来解决这个问题。",
            "基于过往经验，这类情况通常可以采用以下策略应对。",
            "您的观点很有见地，我想补充几点看法。",
            "为了更好地协助您，能否提供更多细节信息？"
        ]
    };
    
    const responsePool = responses[personality] || responses.default;
    const randomIndex = Math.floor(Math.random() * responsePool.length);
    
    return responsePool[randomIndex];
}

// 处理文件选择
chatFileInput.addEventListener('change', function(event) {
    const files = Array.from(event.target.files);
    if (files.length > 0) {
        // 清除之前选择的文件夹
        chatFolderInput.value = '';
        selectedFiles = files;
        updateFileInfo();
    }
});

// 处理文件夹选择
chatFolderInput.addEventListener('change', function(event) {
    const files = Array.from(event.target.files);
    if (files.length > 0) {
        // 清除之前选择的文件
        chatFileInput.value = '';
        selectedFiles = files;
        updateFileInfo();
    }
});

// 更新文件信息显示
function updateFileInfo() {
    if (selectedFiles.length === 0) {
        fileInfoSpan.textContent = '未选择文件';
        clearSelectionButton.style.display = 'none';
        uploadButton.disabled = true;
        return;
    }
    
    if (selectedFiles.length === 1) {
        fileInfoSpan.textContent = selectedFiles[0].name;
    } else {
        fileInfoSpan.textContent = `已选择 ${selectedFiles.length} 个文件`;
    }
    
    clearSelectionButton.style.display = 'inline-block';
    uploadButton.disabled = false;
}

// 清除文件选择
clearSelectionButton.addEventListener('click', function() {
    chatFileInput.value = '';
    chatFolderInput.value = '';
    selectedFiles = [];
    updateFileInfo();
});

// 上传聊天记录
async function uploadChatHistory() {
    if (selectedFiles.length === 0) {
        showUploadStatus('请先选择文件或文件夹', 'error');
        return;
    }
    
    uploadButton.disabled = true;
    uploadButton.textContent = '上传中...';
    
    try {
        // 显示进度条
        showUploadStatus('正在处理文件...', 'progress');
        
        // 分离数据库文件和其他文件
        const dbFiles = selectedFiles.filter(file => file.name.endsWith('.db'));
        const otherFiles = selectedFiles.filter(file => !file.name.endsWith('.db'));
        
        let totalProcessed = 0;
        const totalFiles = selectedFiles.length;
        
        // 处理非数据库文件
        const allChatData = [];
        for (const file of otherFiles) {
            try {
                // 更新进度
                const progress = Math.round((totalProcessed / totalFiles) * 100);
                updateProgress(progress);
                
                // 读取并解析文件
                const fileContent = await readFileContent(file);
                const chatData = parseChatData(fileContent, file.type, file.name);
                allChatData.push(...chatData);
                
                totalProcessed++;
            } catch (error) {
                console.warn(`处理文件 ${file.name} 时出错:`, error);
                totalProcessed++;
            }
        }
        
        // 处理数据库文件
        const dbResults = [];
        for (const file of dbFiles) {
            try {
                // 更新进度
                const progress = Math.round((totalProcessed / totalFiles) * 100);
                updateProgress(progress);
                
                // 上传数据库文件
                const dbResult = await uploadDBFile(file);
                dbResults.push(dbResult);
                
                totalProcessed++;
            } catch (error) {
                console.warn(`处理数据库文件 ${file.name} 时出错:`, error);
                totalProcessed++;
            }
        }
        
        // 更新进度到100%
        updateProgress(100);
        
        // 准备响应信息
        let message = '';
        let totalCount = 0;
        
        if (allChatData.length > 0) {
            // 上传普通聊天数据
            const uploadResult = await uploadChatDataToServer(allChatData);
            if (uploadResult.success) {
                message += `普通消息: ${uploadResult.regular_messages} 条\n`;
                totalCount += uploadResult.regular_messages;
            }
        }
        
        if (dbResults.length > 0) {
            message += `数据库文件: ${dbResults.length} 个\n`;
            totalCount += dbResults.length;
            
            // 添加解析结果
            for (const result of dbResults) {
                if (result.parsed_messages > 0) {
                    message += `  ├─ ${result.filename} (解析出 ${result.parsed_messages} 条消息)\n`;
                } else {
                    message += `  ├─ ${result.filename} (已上传，等待解析)\n`;
                }
            }
        }
        
        // 修复判断条件：即使没有解析出消息，只要文件上传成功也算有效
        if (totalCount === 0 && allChatData.length === 0 && dbResults.length === 0) {
            showUploadStatus('未能从选中的文件中提取到有效的聊天记录', 'error');
            uploadButton.disabled = false;
            uploadButton.textContent = '上传';
            return;
        }
        
        // 即使没有解析出消息，也要显示上传成功的状态
        if (totalCount === 0) {
            message = '文件已成功上传，但未解析出聊天记录\n';
            message += `├─ 普通文件: ${allChatData.length} 条消息\n`;
            message += `├─ 数据库文件: ${dbResults.length} 个\n`;
            totalCount = allChatData.length + dbResults.length;
        }
        
        showUploadStatus(`成功处理 ${totalCount} 项数据\n${message}`, 'success');
        addMessage(`已成功处理上传的数据 (${totalCount} 项)`, false);
        
        // 清空文件选择
        chatFileInput.value = '';
        chatFolderInput.value = '';
        selectedFiles = [];
        updateFileInfo();
        uploadButton.disabled = false;
        uploadButton.textContent = '上传';
        
    } catch (error) {
        console.error('上传失败:', error);
        showUploadStatus('文件处理失败，请检查文件格式', 'error');
        uploadButton.disabled = false;
        uploadButton.textContent = '上传';
    }
}

// 读取文件内容
function readFileContent(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = event => resolve(event.target.result);
        reader.onerror = error => reject(error);
        reader.readAsText(file);
    });
}

// 解析聊天数据
function parseChatData(content, fileType, fileName) {
    try {
        if (fileType === 'application/json' || fileName.endsWith('.json') || 
            (typeof content === 'string' && content.trim().startsWith('{')) || 
            (typeof content === 'string' && content.trim().startsWith('['))) {
            // JSON格式
            const data = JSON.parse(content);
            return Array.isArray(data) ? data : [data];
        } else {
            // TXT格式 - 按行分割
            const lines = content.split('\n').filter(line => line.trim() !== '');
            return lines.map((line, index) => ({
                id: index,
                content: line.trim(),
                timestamp: new Date().toISOString()
            }));
        }
    } catch (error) {
        throw new Error('文件格式不正确');
    }
}

// 上传数据库文件
async function uploadDBFile(file) {
    try {
        // 创建FormData对象
        const formData = new FormData();
        formData.append('dbfile', file);
        formData.append('user_id', 'default_user');
        
        // 发送请求到正确的端口
        const response = await fetch('http://localhost:8080/api/upload-db-file', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const result = await response.json();
        console.log('数据库文件上传结果:', result);
        return result;
    } catch (error) {
        console.error('上传数据库文件失败:', error);
        throw error;
    }
}

// 上传聊天数据到服务器
async function uploadChatDataToServer(chatData) {
    // 实际的API调用到正确的端口
    try {
        console.log('准备上传聊天数据，条数:', chatData.length);
        const response = await fetch('http://localhost:8080/api/upload-chat-data', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                data: chatData,
                user_id: 'default_user'
            })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const result = await response.json();
        console.log('聊天数据上传结果:', result);
        return result;
    } catch (error) {
        console.error('API调用失败:', error);
        return {
            success: false,
            message_count: 0,
            message: '网络错误，请稍后重试'
        };
    }
}

// 显示上传状态
function showUploadStatus(message, type) {
    // 移除之前的状态消息
    const existingStatus = document.querySelector('.upload-status');
    if (existingStatus) {
        existingStatus.remove();
    }
    
    const statusDiv = document.createElement('div');
    statusDiv.className = `upload-status upload-${type}`;
    statusDiv.textContent = message;
    statusDiv.style.whiteSpace = 'pre-line'; // 支持换行
    
    // 如果是进度类型，添加进度条
    if (type === 'progress') {
        const progressBar = document.createElement('div');
        progressBar.className = 'progress-bar';
        const progressFill = document.createElement('div');
        progressFill.className = 'progress-fill';
        progressFill.id = 'progressFill';
        progressBar.appendChild(progressFill);
        statusDiv.appendChild(progressBar);
    }
    
    // 插入到文件上传区域后面
    document.querySelector('.file-upload-section').appendChild(statusDiv);
    
    // 错误和成功消息5秒后自动移除
    if (type === 'error' || type === 'success') {
        setTimeout(() => {
            if (statusDiv.parentNode) {
                statusDiv.remove();
            }
        }, 5000);
    }
}

// 更新进度条
function updateProgress(percent) {
    const progressFill = document.getElementById('progressFill');
    if (progressFill) {
        progressFill.style.width = `${percent}%`;
    }
}

// 事件监听器
sendButton.addEventListener('click', sendMessage);

messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// 文件上传按钮事件
uploadButton.addEventListener('click', uploadChatHistory);

// 页面加载完成后聚焦到输入框
document.addEventListener('DOMContentLoaded', () => {
    messageInput.focus();
    
    // 添加欢迎消息
    setTimeout(() => {
        addMessage("你好！我是你的个性化情感伴侣AI。请选择一种对话风格，然后我们可以开始聊天了。", false);
    }, 1000);
});