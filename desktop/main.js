const { app, BrowserWindow, Tray, Menu, nativeImage, screen, ipcMain } = require('electron');
const path = require('path');

let mainWindow = null;
let tray = null;
let isQuitting = false;

function createWindow() {
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;

    mainWindow = new BrowserWindow({
        width: 400,
        height: 500,
        x: width - 420,
        y: height - 540,
        transparent: true,
        frame: false,
        resizable: false,
        alwaysOnTop: true,
        skipTaskbar: true,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: false,
            contextIsolation: true
        }
    });

    mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
    mainWindow.setIgnoreMouseEvents(true, { forward: true });

    // 窗口失焦时透明度略微降低（可选）
    mainWindow.on('blur', () => { mainWindow.setOpacity(0.85); });
    mainWindow.on('focus', () => { mainWindow.setOpacity(1); });
}

function createTray() {
    const iconPath = path.join(__dirname, 'assets', 'icons', 'tray.png');
    const icon = nativeImage.createFromPath(iconPath);
    tray = new Tray(icon);

    const contextMenu = Menu.buildFromTemplate([
        {
            label: '切换角色',
            submenu: [
                { label: '户山香澄', click: () => {
                    mainWindow.webContents.send('switch-role', 'kasumi');
                }},
                { label: '丸山彩', click: () => {
                    mainWindow.webContents.send('switch-role', 'maruyama');
                }},
                { label: '弦卷心', click: () => {
                    mainWindow.webContents.send('switch-role', 'tsugumi');
                }}
            ]
        },
        { type: 'separator' },
        { label: '显示/隐藏', click: () => {
            mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show();
        }},
        { type: 'separator' },
        { label: '退出', click: () => {
            isQuitting = true;
            app.quit();
        }}
    ]);
    tray.setContextMenu(contextMenu);
    tray.setToolTip('香澄桌宠');
}

app.whenReady().then(() => {
    createWindow();
    createTray();

    // 监听渲染进程发来的切换鼠标穿透请求
    ipcMain.on('toggle-ignore-mouse', () => {
        if (mainWindow) {
            const current = mainWindow.isIgnoringMouseEvents();
            mainWindow.setIgnoreMouseEvents(!current, { forward: true });
            // 通知渲染进程状态变化（可选）
            mainWindow.webContents.send('ignore-mouse-toggled', !current);
        }
    });
});

app.on('window-all-closed', () => {
    if (!isQuitting) app.quit();
});
app.on('before-quit', () => { isQuitting = true; });