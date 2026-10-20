// Electron wrapper (untested here: needs `npm install` with internet). Renderer has no DB access; it only talks to the local login-protected API.
const {app,BrowserWindow}=require('electron'),{spawn}=require('child_process'),path=require('path');let py;
app.whenReady().then(()=>{py=spawn(process.platform=='win32'?'python':'python3',[path.join(__dirname,'server.py')],{env:{...process.env,POS_DB:path.join(app.getPath('userData'),'dairy.db')}});
setTimeout(()=>new BrowserWindow({width:1280,height:800,webPreferences:{contextIsolation:true,nodeIntegration:false,preload:path.join(__dirname,'preload.js')}}).loadURL('http://127.0.0.1:8000'),1500)});
app.on('window-all-closed',()=>{py&&py.kill();app.quit()});
