require('electron').contextBridge.exposeInMainWorld('shell',{platform:process.platform});
