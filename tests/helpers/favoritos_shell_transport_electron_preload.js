'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// These capabilities belong only to the isolated fixture and never reach the JK backend.
contextBridge.exposeInMainWorld('electronAPI', {
  executeEmbeddedMlBrowser: code => ipcRenderer.invoke('favoritos-transport-fixture:execute', code),
  getEmbeddedMlBrowserState: () => ipcRenderer.invoke('favoritos-transport-fixture:state'),
});
