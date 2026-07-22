// ============================================================
// 桌宠渲染进程 - 支持多角色 Live2D（占位版）
// ============================================================

const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const bubble = document.getElementById('bubble');

// ========== 角色配置加载 ==========
let roleConfigs = {};
let activeRoleId = 'kasumi';
let roleModels = {};
let isSpeaking = false;

fetch('./characters.json')
    .then(res => res.json())
    .then(data => {
        roleConfigs = data.roles;
        // 默认加载第一个角色
        loadRole(activeRoleId);
        console.log('角色配置加载完成', Object.keys(roleConfigs));
    })
    .catch(err => console.error('角色配置加载失败', err));

// ========== Canvas 尺寸适配 ==========
function resizeCanvas() {
    const rect = document.getElementById('container').getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
}
resizeCanvas();
window.addEventListener('resize', resizeCanvas);

// ========== 多角色管理 ==========
function loadRole(roleId) {
    const config = roleConfigs[roleId];
    if (!config) return;

    // 如果已加载，则直接设置为可见
    if (roleModels[roleId]) {
        for (const [id, model] of Object.entries(roleModels)) {
            model.visible = (id === roleId);
        }
        activeRoleId = roleId;
        return;
    }

    // 占位模型（后续替换为 Live2D 模型加载）
    const placeholderModel = {
        visible: true,
        draw: (ctx, x, y, color) => {
            ctx.save();
            ctx.translate(x, y);
            // 头
            ctx.beginPath();
            ctx.arc(0, -40, 50, 0, Math.PI * 2);
            ctx.fillStyle = color || '#FFB7C5';
            ctx.fill();
            ctx.strokeStyle = '#333';
            ctx.lineWidth = 2;
            ctx.stroke();
            // 身体
            ctx.beginPath();
            ctx.ellipse(0, 20, 30, 20, 0, 0, Math.PI * 2);
            ctx.fillStyle = color || '#FFB7C5';
            ctx.fill();
            ctx.stroke();
            // 头发（简化）
            ctx.beginPath();
            ctx.moveTo(-30, -60);
            ctx.quadraticCurveTo(-40, -100, 0, -110);
            ctx.quadraticCurveTo(40, -100, 30, -60);
            ctx.fillStyle = '#D4A373';
            ctx.fill();
            ctx.stroke();
            ctx.restore();
        }
    };

    roleModels[roleId] = placeholderModel;

    // 隐藏其他模型
    for (const [id, model] of Object.entries(roleModels)) {
        model.visible = (id === roleId);
    }
    activeRoleId = roleId;
}

function switchRole(roleId) {
    if (!roleConfigs[roleId] || roleId === activeRoleId) return;
    loadRole(roleId);
    // 可在此调整气泡位置
}

// ========== 渲染循环 ==========
function renderLoop() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const [roleId, model] of Object.entries(roleModels)) {
        if (model.visible) {
            const config = roleConfigs[roleId];
            if (config && config.position) {
                const x = config.position.x * canvas.width;
                const y = config.position.y * canvas.height;
                if (model.draw) {
                    model.draw(ctx, x, y, config.color);
                }
            }
        }
    }
    requestAnimationFrame(renderLoop);
}
renderLoop();

// ========== 对话气泡 ==========
let speakTimer = null;
function showBubble(text, duration = 4000) {
    bubble.textContent = text;
    bubble.style.display = 'block';
    clearTimeout(speakTimer);
    speakTimer = setTimeout(() => {
        bubble.style.display = 'none';
    }, duration);
}

// ========== WebSocket 连接 ==========
const ws = new WebSocket('ws://localhost:8000/ws/chat');

ws.onopen = () => {
    console.log('桌宠已连接到 MemBrain 后端');
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'message') {
        // 如果消息中带有 role_id，自动切换角色
        if (data.role_id && roleConfigs[data.role_id]) {
            switchRole(data.role_id);
        }
        showBubble(data.content, 5000);
        isSpeaking = true;
        setTimeout(() => { isSpeaking = false; }, 3000);
    }
};

function sendMessage(text) {
    if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            user_id: 'desktop_user',
            message: text,
            tts: false
        }));
    }
}

// ========== 鼠标交互 ==========
let clickCount = 0;
let clickTimer = null;

canvas.addEventListener('click', () => {
    clickCount++;
    if (clickCount === 1) {
        clickTimer = setTimeout(() => {
            clickCount = 0;
            const text = prompt('对香澄说：');
            if (text?.trim()) {
                sendMessage(text.trim());
            }
        }, 300);
    } else if (clickCount === 2) {
        clearTimeout(clickTimer);
        clickCount = 0;
        // 双击切换鼠标穿透（通过 IPC 通知主进程）
        if (window.electron) {
            window.electron.send('toggle-ignore-mouse');
        }
    }
});

// ========== IPC 监听（主进程发来的角色切换） ==========
if (window.electron) {
    window.electron.receive('switch-role', (roleId) => {
        if (roleConfigs[roleId]) {
            switchRole(roleId);
            const config = roleConfigs[roleId];
            showBubble(`切换至 ${config.name}`, 1500);
        }
    });
}

// ========== 启动后显示欢迎语 ==========
setTimeout(() => {
    showBubble('你好呀！我是香澄✨', 3000);
}, 1000);

// ========== 主动打招呼（每30秒） ==========
setInterval(() => {
    if (isSpeaking) return;
    const greetings = [
        '今天过得怎么样？',
        '想听我唱歌吗？🎤',
        '饿了吗？我们去吃炸薯条吧！🍟',
        '有没有发现什么闪闪发光的事情呀✨'
    ];
    const msg = greetings[Math.floor(Math.random() * greetings.length)];
    showBubble(msg, 3000);
}, 30000);