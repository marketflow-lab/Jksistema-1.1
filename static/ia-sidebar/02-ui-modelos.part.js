  function _definirTextoMsg(el, texto) {
    if (!el) return;
    el.innerHTML = _renderTexto(texto);
    _ativarFallbacksImagem(el);
  }

  const CSS = `
  #jk-left-sidebar-hotspot{position:fixed;top:0;left:0;bottom:0;width:18px;z-index:10000;pointer-events:auto;}
  #jk-left-sidebar-hotspot::before{content:"";position:absolute;top:0;left:0;bottom:0;width:8px;background:transparent;}
  #jk-left-sidebar-menu{position:fixed;top:50%;left:10px;width:360px;max-height:calc(100vh - 28px);overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;z-index:10001;
    display:flex;flex-direction:column;align-items:flex-start;gap:8px;padding:7px 0;transform:translate(calc(-100% - 28px),-50%);
    opacity:0;pointer-events:none;transition:transform .2s cubic-bezier(.4,0,.2,1),opacity .16s ease;scrollbar-width:none;-ms-overflow-style:none;}
  #jk-left-sidebar-menu::-webkit-scrollbar{width:0;height:0;display:none;}
  #jk-left-sidebar-menu::-webkit-scrollbar-track{background:transparent;}
  #jk-left-sidebar-menu::-webkit-scrollbar-thumb{background:transparent;}
  #jk-left-sidebar-hotspot:hover #jk-left-sidebar-menu,
  #jk-left-sidebar-hotspot:focus-within #jk-left-sidebar-menu{transform:translate(0,-50%);opacity:1;pointer-events:auto;}
  .jk-left-module-link{position:relative;width:344px;min-height:46px;display:flex;align-items:center;text-decoration:none;color:#e8fffb;overflow:visible;
    border:0;background:transparent;outline:none;padding:0;cursor:pointer;font:inherit;}
  .jk-left-module-icon{position:relative;z-index:2;width:46px;height:46px;border-radius:14px;border:1px solid rgba(120,227,212,.32);
    display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#0f6bbd,#13b99a);box-shadow:0 4px 16px rgba(19,196,160,.34);
    font-size:1.25rem;line-height:1;transform:scale(1);transform-origin:left center;will-change:transform;
    transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;}
  .jk-left-module-glyph{display:inline-flex;align-items:center;justify-content:center;line-height:1;transform:rotate(0deg) scale(1);transform-origin:center center;will-change:transform;animation:none;}
  .jk-left-module-label{position:absolute;left:78px;top:50%;z-index:5;min-width:118px;max-width:244px;min-height:34px;display:flex;align-items:center;
    padding:0 13px 0 14px;border-radius:12px;border:1px solid rgba(120,227,212,.3);
    background:linear-gradient(90deg,rgba(8,43,59,.96),rgba(6,71,84,.92));box-shadow:0 8px 22px rgba(0,0,0,.34);
    color:#e8fffb;font-size:.76rem;font-weight:900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
    transform:translate(-18px,-50%) scaleX(.25);transform-origin:left center;opacity:0;transition:transform .16s cubic-bezier(.2,.9,.2,1),opacity .12s ease;}
  @keyframes jkLeftModuleGlyphSpin{from{transform:rotate(0deg) scale(1.14);}to{transform:rotate(360deg) scale(1.14);}}
  .jk-left-module-link:hover .jk-left-module-icon,
  .jk-left-module-icon:hover,
  .jk-left-module-link:focus-visible .jk-left-module-icon{transform:translateX(8px) scale(1.7);border-color:rgba(255,255,255,.82);box-shadow:0 0 0 5px rgba(120,227,212,.22),0 10px 28px rgba(19,196,160,.58);}
  .jk-left-module-link:hover .jk-left-module-glyph,
  .jk-left-module-icon:hover .jk-left-module-glyph,
  .jk-left-module-link:focus-visible .jk-left-module-glyph{animation:jkLeftModuleGlyphSpin .58s linear infinite;}
  .jk-left-module-link:hover .jk-left-module-label,
  .jk-left-module-link:focus-visible .jk-left-module-label{transform:translate(0,-50%) scaleX(1);opacity:1;}
  .jk-left-module-link.modulo-atual{display:none;}
  .jk-left-module-group{position:relative;width:344px;min-height:46px;overflow:visible;}
  .jk-left-module-group-trigger{width:344px;text-align:left;}
  .jk-left-module-group:hover .jk-left-module-label,
  .jk-left-module-group:focus-within .jk-left-module-label,
  .jk-left-module-group.submenu-aberto .jk-left-module-label{transform:translate(0,-50%) scaleX(1);opacity:1;}
  .jk-left-module-submenu{position:absolute;left:78px;top:39px;z-index:9;min-width:178px;max-width:244px;display:flex;flex-direction:column;gap:4px;
    padding:7px;border-radius:13px;border:1px solid rgba(120,227,212,.32);
    background:linear-gradient(180deg,rgba(8,43,59,.98),rgba(6,71,84,.96));box-shadow:0 12px 28px rgba(0,0,0,.38);
    transform:translate(-18px,-6px) scale(.96);transform-origin:left top;opacity:0;pointer-events:none;
    transition:transform .16s cubic-bezier(.2,.9,.2,1),opacity .12s ease;}
  .jk-left-module-group:hover .jk-left-module-submenu,
  .jk-left-module-group:focus-within .jk-left-module-submenu,
  .jk-left-module-group.submenu-aberto .jk-left-module-submenu{transform:translate(0,0) scale(1);opacity:1;pointer-events:auto;}
  .jk-left-submodule-link{display:flex;align-items:center;gap:8px;min-height:32px;padding:6px 8px;border-radius:9px;color:#e8fffb;text-decoration:none;
    font-size:.74rem;font-weight:900;line-height:1.12;white-space:nowrap;outline:none;}
  .jk-left-submodule-link:hover,
  .jk-left-submodule-link:focus-visible{background:rgba(120,227,212,.14);box-shadow:inset 0 0 0 1px rgba(120,227,212,.22);}
  .jk-left-submodule-icon{width:23px;height:23px;display:inline-flex;align-items:center;justify-content:center;border-radius:8px;background:rgba(255,255,255,.08);font-size:.94rem;flex:0 0 auto;}
  #jk-right-sidebar-hotspot{position:fixed;top:0;right:0;bottom:0;width:18px;z-index:10000;pointer-events:auto;}
  #jk-right-sidebar-hotspot::before{content:"";position:absolute;top:0;right:0;bottom:0;width:8px;background:transparent;}
  #jk-right-sidebar-menu{position:fixed;top:50%;right:3px;display:flex;flex-direction:column;gap:10px;
    transform:translate(calc(100% + 22px),-50%);opacity:0;pointer-events:none;
    transition:transform .2s cubic-bezier(.4,0,.2,1),opacity .16s ease;}
  #jk-right-sidebar-hotspot:hover #jk-right-sidebar-menu,
  #jk-right-sidebar-hotspot:focus-within #jk-right-sidebar-menu,
  #jk-right-sidebar-hotspot.tem-alerta #jk-right-sidebar-menu,
  #jk-right-sidebar-hotspot.menu-aberto #jk-right-sidebar-menu{transform:translate(0,-50%);opacity:1;pointer-events:auto;}
  .jk-right-sidebar-icon{position:relative;width:50px;height:50px;border-radius:14px;border:1px solid rgba(120,227,212,.32);
    background:linear-gradient(145deg,#1888ff,#13c4a0);color:#fff;font-size:1.35rem;cursor:pointer;
    box-shadow:0 4px 18px rgba(19,196,160,.42);display:flex;align-items:center;justify-content:center;
    transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;}
  .jk-right-sidebar-icon svg{width:24px;height:24px;stroke:currentColor;stroke-width:2.35;fill:none;stroke-linecap:round;stroke-linejoin:round;filter:drop-shadow(0 1px 1px rgba(0,0,0,.18));}
  .jk-right-sidebar-icon:hover{transform:translateX(-2px) scale(1.04);box-shadow:0 8px 24px rgba(19,196,160,.5);}
  .jk-right-sidebar-icon.ativo{border-color:rgba(255,255,255,.72);box-shadow:0 0 0 3px rgba(120,227,212,.18),0 8px 24px rgba(19,196,160,.48);}
  .jk-right-sidebar-icon.piscando{animation:jkRightSidebarBlink .9s ease-in-out infinite;border-color:rgba(255,255,255,.82);}
  @keyframes jkRightSidebarBlink{
    0%,100%{filter:brightness(1);box-shadow:0 4px 18px rgba(19,196,160,.42);}
    50%{filter:brightness(1.3);box-shadow:0 0 0 5px rgba(239,68,68,.18),0 0 28px rgba(239,68,68,.72);}
  }
  #jk-msg-fab{background:linear-gradient(145deg,#5b8cff,#21b8a3);}
  #jk-codex-fab{background:#101c37;font-size:0;font-weight:900;padding:0;overflow:hidden;border-radius:999px;}
  #jk-codex-fab.is-hidden{display:none;}
  .jk-codex-avatar{display:block;width:100%;height:100%;object-fit:cover;}
  #jk-codex-fab .jk-codex-avatar,.jk-codex-icon .jk-codex-avatar{transform:scale(1.12);transform-origin:center;}
  #jk-msg-badge{position:absolute;top:5px;right:5px;min-width:17px;height:17px;padding:0 5px;border-radius:999px;
    background:#ef4444;color:#fff;border:2px solid #062a22;font-size:.62rem;font-weight:900;line-height:13px;text-align:center;display:none;}
  #jk-msg-badge:not(:empty){display:block;}
  #jk-ia-panel{--jk-ia-panel-width:360px;position:fixed;top:0;right:0;bottom:0;z-index:9999;width:var(--jk-ia-panel-width);min-width:300px;max-width:96vw;height:100vh;height:100dvh;max-height:100vh;max-height:100dvh;min-height:0;
    background:linear-gradient(165deg,#081b2e,#062a22);border-left:1px solid rgba(106,225,203,.28);
    display:flex;flex-direction:column;box-shadow:-6px 0 32px rgba(0,0,0,.55);
    transform:translateX(110%);transition:transform .25s cubic-bezier(.4,0,.2,1);overflow:hidden;}
  #jk-ia-panel *,#jk-ia-panel *::before,#jk-ia-panel *::after{box-sizing:border-box;}
  #jk-ia-panel.aberto{transform:translateX(0);}
  #jk-msg-panel{--jk-msg-panel-width:370px;position:fixed;top:0;right:0;bottom:0;z-index:9999;width:var(--jk-msg-panel-width);min-width:320px;max-width:96vw;
    background:#071923;border-left:1px solid rgba(39,224,205,.32);
    display:flex;flex-direction:column;box-shadow:-6px 0 28px rgba(0,0,0,.48);
    transform:translateX(110%);transition:transform .25s cubic-bezier(.4,0,.2,1);
    -webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;isolation:isolate;}
  #jk-msg-panel *,#jk-msg-panel *::before,#jk-msg-panel *::after{box-sizing:border-box;text-shadow:none;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;}
  #jk-msg-panel.aberto{transform:translateX(0);}
  #jk-msg-panel::before{content:"";position:absolute;inset:0 0 auto 0;height:132px;pointer-events:none;opacity:.06;
    background:
      radial-gradient(circle at 28px 24px,transparent 0 9px,rgba(169,206,213,.75) 10px 11px,transparent 12px),
      radial-gradient(circle at 118px 14px,transparent 0 7px,rgba(169,206,213,.55) 8px 9px,transparent 10px),
      linear-gradient(135deg,transparent 0 16px,rgba(169,206,213,.45) 17px 18px,transparent 19px);
    background-size:92px 56px,116px 62px,78px 48px;}
  #jk-msg-header{position:relative;display:flex;align-items:center;gap:9px;padding:18px 16px 15px;border-bottom:1px solid rgba(39,224,205,.24);background:#071923;}
  .jk-msg-title-wrap{flex:1 1 auto;display:flex;align-items:center;gap:10px;min-width:0;}
  .jk-msg-header-icon{flex:0 0 35px;width:35px;height:35px;border-radius:12px;display:flex;align-items:center;justify-content:center;color:#c8d5da;background:rgba(164,181,189,.08);}
  .jk-msg-header-icon svg{width:22px;height:22px;stroke:currentColor;stroke-width:2.25;fill:none;stroke-linecap:round;stroke-linejoin:round;}
  #jk-msg-header h3{margin:0;color:#f5fbff;font-size:1.42rem;font-weight:900;line-height:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:.01em;}
  .jk-msg-header-subtitle{display:block;color:#8ca3ad;font-size:.58rem;font-weight:800;margin-top:4px;letter-spacing:.02em;}
  .jk-msg-hbtn{border:1px solid rgba(39,224,205,.25);background:rgba(18,65,79,.8);color:#5de7dd;cursor:pointer;font-size:.82rem;padding:0;border-radius:9px;line-height:1;min-height:26px;min-width:26px;height:26px;display:flex;align-items:center;justify-content:center;}
  .jk-msg-hbtn:hover{background:rgba(24,97,112,.86);border-color:rgba(39,224,205,.48);transform:translateY(-1px);}
  #jk-msg-body{position:relative;flex:1 1 auto;overflow-y:auto;padding:12px 12px 10px;display:flex;flex-direction:column;gap:12px;background:#061923;scrollbar-width:thin;scrollbar-color:rgba(120,227,212,.34) transparent;}
  #jk-msg-body::-webkit-scrollbar{width:7px;}
  #jk-msg-body::-webkit-scrollbar-thumb{background:rgba(120,227,212,.28);border-radius:999px;}
  .jk-msg-status{min-height:16px;color:#8aa5ad;font-size:.68rem;font-weight:800;line-height:1.35;}
  #jk-msg-main-view{display:flex;flex-direction:column;gap:12px;}
  #jk-msg-chat-header{display:none;align-items:center;gap:10px;padding:8px 0 8px;position:sticky;top:0;z-index:6;
    background:linear-gradient(165deg,rgba(9,29,49,.98),rgba(6,42,34,.98));box-shadow:0 10px 18px rgba(0,0,0,.24);}
  #jk-msg-chat-header.ativo{display:flex;}
  #jk-msg-back{flex:0 0 34px;width:34px;height:34px;border-radius:8px;border:1px solid rgba(120,227,212,.42);background:rgba(35,142,165,.44);color:#e8fffb;font-weight:900;cursor:pointer;box-shadow:0 8px 18px rgba(0,0,0,.26);}
  #jk-msg-back:hover{background:rgba(36,161,160,.32);border-color:rgba(120,227,212,.64);}
  #jk-msg-chat-title{flex:1 1 auto;min-width:0;color:#fff;font-size:.86rem;font-weight:900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  #jk-msg-video-call{flex:0 0 36px;width:36px;height:36px;border-radius:999px;border:1px solid rgba(85,201,109,.45);background:rgba(85,201,109,.18);color:#bfffd0;font-size:1rem;font-weight:900;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 8px 18px rgba(0,0,0,.24);}
  #jk-msg-video-call:hover{background:rgba(85,201,109,.28);border-color:rgba(85,201,109,.72);transform:translateY(-1px);}
  #jk-msg-video-call:disabled{cursor:not-allowed;opacity:.45;filter:saturate(.6);transform:none;}
  #jk-msg-history{display:none;flex:1 1 auto;flex-direction:column;gap:8px;padding:2px 0 8px;}
  #jk-msg-history.ativo{display:flex;}
  #jk-msg-typing{display:none;color:#9ee8df;font-size:.72rem;font-weight:900;min-height:17px;padding:0 2px 6px;}
  #jk-msg-typing.ativo{display:block;}
  .jk-msg-bubble{max-width:86%;min-width:0;overflow:hidden;border:1px solid rgba(120,227,212,.22);border-radius:13px;padding:8px 10px;color:#e8fffb;background:rgba(23,47,70,.55);font-size:.78rem;line-height:1.42;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;}
  .jk-msg-bubble.me{align-self:flex-end;background:linear-gradient(165deg,#1888ff,#0f65d8);border-color:transparent;color:#fff;border-bottom-right-radius:4px;}
  .jk-msg-bubble.other{align-self:flex-start;border-bottom-left-radius:4px;}
  .jk-msg-bubble-meta{display:block;margin-top:4px;font-size:.62rem;color:rgba(232,255,251,.72);white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis;}
  .jk-msg-bubble-meta.local-alert{color:#ffd7a3;}
  .jk-msg-call-card{display:flex;align-items:center;gap:9px;min-width:min(210px,100%);border:0;border-radius:10px;background:rgba(3,31,34,.24);padding:4px 2px;}
  .jk-msg-bubble.me .jk-msg-call-card{background:rgba(0,42,82,.18);}
  .jk-msg-call-icon{flex:0 0 34px;width:34px;height:34px;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;background:#55c96d;color:#061b12;box-shadow:0 0 0 4px rgba(85,201,109,.14);font-size:.92rem;}
  .jk-msg-call-info{min-width:0;flex:1 1 auto;display:grid;gap:1px;}
  .jk-msg-call-title{color:#f4fff8;font-weight:900;font-size:.82rem;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-msg-call-text{color:rgba(232,255,251,.82);font-size:.68rem;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-msg-call-actions{flex:0 0 auto;display:flex;align-items:center;justify-content:flex-end;}
  .jk-msg-call-join{border:0;border-radius:999px;background:#55c96d;color:#061b12;font-weight:900;font-size:.68rem;min-height:28px;padding:0 10px;cursor:pointer;}
  .jk-msg-call-join:hover{filter:brightness(1.08);transform:translateY(-1px);}
  .jk-msg-attachments{display:grid;gap:6px;margin-top:7px;}
  .jk-msg-attachment{border:1px solid rgba(120,227,212,.24);border-radius:8px;background:rgba(3,22,32,.48);padding:6px;color:#dffefa;font-size:.7rem;overflow:hidden;}
  .jk-msg-image-thumb{display:block;border:0;background:transparent;padding:0;margin:0;cursor:zoom-in;max-width:100%;border-radius:7px;}
  .jk-msg-image-thumb img{display:block;max-width:190px;max-height:150px;border-radius:7px;object-fit:contain;background:rgba(0,0,0,.22);transition:filter .15s ease,transform .15s ease;}
  .jk-msg-image-thumb:hover img{filter:brightness(1.08);transform:scale(1.01);}
  .jk-msg-attachment audio{width:210px;max-width:100%;height:34px;display:block;}
  .jk-msg-attachment a{color:#9ee8df;text-decoration:none;font-weight:900;word-break:break-word;}
  .jk-msg-attachment a:hover{text-decoration:underline;}
  .jk-msg-section{display:flex;flex-direction:column;gap:9px;}
  .jk-msg-inbox-section.jk-msg-section-empty{display:none;}
  .jk-msg-users-section{border:1px solid rgba(39,224,205,.24);border-radius:14px;background:#071f2a;padding:12px;}
  .jk-msg-section-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:3px;}
  .jk-msg-section-title{color:#45e4d7;font-size:.64rem;font-weight:900;text-transform:uppercase;letter-spacing:.08em;}
  .jk-msg-count-pill{min-width:76px;height:22px;border-radius:999px;border:1px solid rgba(39,224,205,.28);background:rgba(35,142,165,.28);color:#bdf9f2;font-size:.55rem;font-weight:900;display:inline-flex;align-items:center;justify-content:center;padding:0 10px;}
  .jk-msg-list{display:flex;flex-direction:column;gap:7px;}
  #jk-msg-online-list{gap:7px;}
  .jk-msg-empty{color:#b9cbd1;font-size:.76rem;opacity:.82;border:1px dashed rgba(120,227,212,.24);border-radius:11px;padding:10px;background:rgba(4,26,35,.36);}
  .jk-msg-user-item,.jk-msg-card{border:1px solid rgba(39,224,205,.26);border-radius:12px;background:#092a35;color:#e8fffb;padding:10px;}
  .jk-msg-user-item{position:relative;display:grid;grid-template-columns:34px minmax(0,1fr) 16px 10px;align-items:center;gap:9px;text-align:left;cursor:pointer;width:100%;min-height:48px;padding:8px 9px;border-radius:12px;box-shadow:inset 0 1px 0 rgba(255,255,255,.02);transition:background .16s ease,border-color .16s ease,transform .16s ease;}
  .jk-msg-user-item:hover,.jk-msg-user-item.ativo,.jk-msg-user-item.self{border-color:rgba(45,232,218,.44);background:linear-gradient(90deg,#11777e,#124b61);}
  .jk-msg-user-item:hover{transform:translateY(-1px);}
  .jk-msg-user-item:disabled{cursor:default;opacity:1;}
  .jk-msg-avatar{width:31px;height:31px;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;color:#fff;font-size:.78rem;font-weight:900;background:#6954f2;box-shadow:0 0 0 1px rgba(255,255,255,.16);}
  .jk-msg-avatar.c1{background:#18bdb8;}
  .jk-msg-avatar.c2{background:#f3a522;}
  .jk-msg-avatar.c3{background:#d84ce8;}
  .jk-msg-avatar.c4{background:#4b8ff4;}
  .jk-msg-avatar.c5{background:#ef5069;}
  .jk-msg-avatar.c6{background:#2fcf84;}
  .jk-msg-avatar.c7{background:#23a5ee;}
  .jk-msg-user-info{min-width:0;display:grid;gap:2px;flex:1 1 auto;}
  .jk-msg-user-name{font-size:.78rem;font-weight:900;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#fff;}
  .jk-msg-user-meta{display:block;color:#9ab0b8;font-size:.62rem;font-weight:800;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-msg-user-item.ativo .jk-msg-user-meta,.jk-msg-user-item.self .jk-msg-user-meta{color:#d3f8f4;}
  .jk-msg-contact-alert{width:11px;height:11px;border-radius:999px;background:#2fe080;border:2px solid rgba(6,29,39,.88);box-shadow:0 0 0 2px rgba(47,224,128,.14),0 0 10px rgba(47,224,128,.58);}
  .jk-msg-user-item.offline .jk-msg-contact-alert,.jk-msg-contact-alert.offline{background:#ff4b6a;box-shadow:0 0 0 2px rgba(255,75,106,.13),0 0 10px rgba(255,75,106,.6);}
  .jk-msg-chevron{color:#7fa2aa;font-size:1rem;line-height:1;font-weight:900;}
  .jk-msg-unread-count{position:absolute;right:21px;top:7px;border-radius:999px;background:#ff4b6a;color:#fff;border:1px solid rgba(255,255,255,.26);font-size:.52rem;font-weight:900;min-width:16px;min-height:15px;padding:1px 4px;display:inline-flex;align-items:center;justify-content:center;box-shadow:0 0 8px rgba(239,68,68,.24);}
  .jk-msg-card{display:grid;gap:6px;}
  .jk-msg-card-title{font-size:.82rem;font-weight:900;color:#fff;}
  .jk-msg-card-meta{font-size:.68rem;color:#9ee8df;}
  .jk-msg-card-text{font-size:.78rem;line-height:1.42;white-space:pre-wrap;color:#e8fffb;overflow-wrap:anywhere;word-break:break-word;}
  .jk-msg-card-actions{display:flex;justify-content:flex-end;}
  .jk-msg-small-btn{border:1px solid rgba(120,227,212,.34);border-radius:8px;background:rgba(35,142,165,.22);color:#e8fffb;font-weight:900;font-size:.72rem;min-height:30px;padding:0 9px;cursor:pointer;}
  .jk-msg-small-btn:hover{border-color:rgba(120,227,212,.7);background:rgba(36,161,160,.3);}
  #jk-msg-compose{border-top:1px solid rgba(39,224,205,.24);padding:9px 10px 11px;background:#071923;display:grid;gap:7px;}
  #jk-msg-selected{color:#8ea8b0;font-size:.68rem;font-weight:800;min-height:16px;padding:0 4px;}
  #jk-msg-tools{display:flex;align-items:center;gap:8px;min-height:44px;}
  .jk-msg-input-pill{flex:1 1 auto;min-width:0;display:flex;align-items:center;gap:4px;min-height:44px;border:1px solid rgba(39,224,205,.16);border-radius:18px;background:#20272b;padding:5px 7px;}
  .jk-msg-tool-btn{border:0;border-radius:999px;background:transparent;color:#9fb2b9;font-weight:900;font-size:1.05rem;min-width:32px;height:32px;padding:0;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;flex:0 0 32px;}
  .jk-msg-tool-btn svg{width:17px;height:17px;stroke:currentColor;stroke-width:2.3;fill:none;stroke-linecap:round;stroke-linejoin:round;}
  .jk-msg-tool-btn:hover,.jk-msg-tool-btn.ativo{background:rgba(39,224,205,.12);color:#45e4d7;}
  #jk-msg-emoji-panel{display:none;grid-template-columns:repeat(8,1fr);gap:4px;border:1px solid rgba(120,227,212,.2);border-radius:8px;padding:6px;background:rgba(3,22,32,.9);}
  #jk-msg-emoji-panel.aberto{display:grid;}
  .jk-msg-emoji-choice{border:0;border-radius:6px;background:rgba(35,142,165,.16);min-height:28px;cursor:pointer;font-size:1rem;}
  .jk-msg-emoji-choice:hover{background:rgba(36,161,160,.28);}
  #jk-msg-anexos{display:flex;flex-wrap:wrap;gap:5px;}
  .jk-msg-anexo-chip{display:inline-flex;align-items:center;gap:5px;max-width:100%;border:1px solid rgba(120,227,212,.26);border-radius:999px;background:rgba(25,120,133,.18);color:#dbfffb;font-size:.68rem;font-weight:800;padding:4px 7px;}
  .jk-msg-anexo-chip span{max-width:210px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-msg-anexo-chip button{border:0;background:transparent;color:#ffd2d2;font-size:.82rem;font-weight:900;cursor:pointer;padding:0;}
  #jk-msg-text{flex:1 1 auto;width:100%;min-width:0;border:0;border-radius:0;background:transparent;color:#eef7f7;font:inherit;font-size:.92rem;font-weight:500;outline:none;padding:5px 2px;min-height:28px;max-height:86px;resize:none;line-height:1.25;overflow-wrap:anywhere;word-break:break-word;}
  #jk-msg-text::placeholder{color:#9fa8ad;}
  #jk-msg-text:focus{box-shadow:none;}
  #jk-msg-send{border:0;border-radius:999px;background:#55c96d;color:#061b12;font-weight:900;font-size:1rem;min-width:44px;width:44px;height:44px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;flex:0 0 44px;padding:0;box-shadow:0 8px 18px rgba(85,201,109,.22);}
  #jk-msg-send svg{width:18px;height:18px;stroke:currentColor;stroke-width:2.45;fill:none;stroke-linecap:round;stroke-linejoin:round;}
  #jk-msg-send:disabled{cursor:not-allowed;opacity:.55;filter:saturate(.55);}
  #jk-msg-call-modal[hidden]{display:none;}
  #jk-msg-call-modal{position:fixed;inset:0;z-index:10050;display:grid;place-items:center;padding:18px;background:rgba(0,0,0,.5);backdrop-filter:blur(5px);}
  .jk-msg-call-dialog{width:min(330px,94vw);border:1px solid rgba(85,201,109,.42);border-radius:18px;background:linear-gradient(160deg,#081923,#0a3327);box-shadow:0 22px 60px rgba(0,0,0,.55);padding:20px 18px;color:#f5fff8;display:grid;gap:14px;text-align:center;}
  .jk-msg-call-avatar{width:72px;height:72px;margin:0 auto;border-radius:999px;display:flex;align-items:center;justify-content:center;background:#55c96d;color:#061b12;font-size:2rem;font-weight:900;box-shadow:0 0 0 8px rgba(85,201,109,.14);animation:jkMsgCallPulse 1.15s ease-in-out infinite;}
  .jk-msg-call-dialog h3{margin:0;font-size:1.18rem;}
  .jk-msg-call-dialog p{margin:0;color:#cfeee0;font-size:.84rem;line-height:1.45;}
  .jk-msg-call-dialog-actions{display:flex;justify-content:center;gap:12px;margin-top:4px;}
  .jk-msg-call-dialog-actions button{border:0;border-radius:999px;min-width:96px;min-height:42px;font-weight:900;cursor:pointer;}
  #jk-msg-call-decline{background:#ef4444;color:#fff;}
  #jk-msg-call-answer{background:#55c96d;color:#061b12;}
  @keyframes jkMsgCallPulse{0%,100%{transform:scale(1);box-shadow:0 0 0 8px rgba(85,201,109,.14);}50%{transform:scale(1.06);box-shadow:0 0 0 14px rgba(85,201,109,.08);}}
  #jk-msg-image-modal[hidden]{display:none;}
  #jk-msg-image-modal{position:fixed;inset:0;z-index:10060;display:grid;grid-template-rows:auto minmax(0,1fr);background:rgba(0,0,0,.88);backdrop-filter:blur(6px);}
  .jk-msg-image-modal-bar{display:flex;align-items:center;gap:8px;min-height:52px;padding:8px 12px;background:rgba(3,14,20,.82);border-bottom:1px solid rgba(120,227,212,.22);}
  .jk-msg-image-modal-title{flex:1 1 auto;min-width:0;color:#effffd;font-size:.84rem;font-weight:900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-msg-image-modal-btn{border:1px solid rgba(120,227,212,.34);border-radius:9px;background:rgba(35,142,165,.28);color:#e8fffb;font-size:.74rem;font-weight:900;min-height:34px;padding:0 10px;cursor:pointer;}
  .jk-msg-image-modal-btn:hover{border-color:rgba(120,227,212,.7);background:rgba(36,161,160,.34);}
  #jk-msg-image-modal-close{min-width:36px;padding:0;font-size:1.2rem;line-height:1;}
  .jk-msg-image-modal-stage{min-width:0;min-height:0;display:grid;place-items:center;padding:14px;overflow:auto;}
  #jk-msg-image-modal-img{display:block;max-width:100%;max-height:100%;object-fit:contain;border-radius:8px;background:rgba(255,255,255,.03);box-shadow:0 18px 60px rgba(0,0,0,.52);}
  #jk-codex-panel{--jk-codex-panel-width:410px;position:fixed;top:0;right:0;bottom:0;z-index:9999;width:var(--jk-codex-panel-width);min-width:330px;max-width:96vw;
    background:#091625;border-left:1px solid rgba(167,139,250,.34);display:flex;flex-direction:column;box-shadow:-6px 0 30px rgba(0,0,0,.5);
    transform:translateX(110%);transition:transform .25s cubic-bezier(.4,0,.2,1);overflow:hidden;}
  #jk-codex-panel *,#jk-codex-panel *::before,#jk-codex-panel *::after{box-sizing:border-box;}
  #jk-codex-panel.aberto{transform:translateX(0);}
  #jk-codex-header{display:flex;align-items:center;gap:10px;min-height:60px;padding:14px;border-bottom:1px solid rgba(167,139,250,.22);background:#0c1b2b;}
  .jk-codex-icon{flex:0 0 38px;width:38px;height:38px;border-radius:12px;display:flex;align-items:center;justify-content:center;background:#101c37;color:#fff;font-weight:900;overflow:hidden;border:1px solid rgba(167,139,250,.38);box-shadow:0 0 0 2px rgba(20,184,166,.08);}
  #jk-codex-header h3{margin:0;color:#f4f2ff;font-size:1.08rem;font-weight:900;line-height:1;}
  #jk-codex-header span{display:block;margin-top:4px;color:#9fb5c7;font-size:.64rem;font-weight:800;}
  .jk-codex-hbtn{border:1px solid rgba(167,139,250,.28);background:rgba(31,41,55,.8);color:#dcd7ff;cursor:pointer;font-size:1rem;padding:0;border-radius:9px;line-height:1;min-height:30px;min-width:30px;height:30px;display:flex;align-items:center;justify-content:center;}
  .jk-codex-hbtn:hover{border-color:rgba(167,139,250,.58);background:rgba(88,70,140,.35);}
  #jk-codex-status-row{display:flex;align-items:center;gap:10px;min-height:30px;max-height:30px;overflow:hidden;padding:0 12px;border-top:1px solid rgba(167,139,250,.16);background:#081524;color:#c7d2fe;}
  #jk-codex-context-pct{flex:0 0 auto;min-width:54px;border:1px solid rgba(20,184,166,.28);border-radius:999px;background:rgba(20,184,166,.09);color:#bffef6;font-size:.66rem;font-weight:900;line-height:20px;text-align:center;padding:0 7px;white-space:nowrap;}
  #jk-codex-status{display:block;flex:1 1 0;min-width:0;color:#c7d2fe;font-size:.72rem;font-weight:800;line-height:30px;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  #jk-codex-history-panel{display:none;flex:0 0 auto;max-height:260px;overflow:auto;padding:8px 10px;border-bottom:1px solid rgba(167,139,250,.18);background:#07131f;scrollbar-width:thin;scrollbar-color:rgba(167,139,250,.12) transparent;}
  #jk-codex-history-panel.ativo{display:grid;gap:7px;}
  #jk-codex-history-panel::-webkit-scrollbar{width:5px;height:5px;}
  #jk-codex-history-panel::-webkit-scrollbar-track{background:transparent;}
  #jk-codex-history-panel::-webkit-scrollbar-thumb{background:rgba(167,139,250,.1);border-radius:999px;}
  .jk-codex-history-head{display:flex;align-items:center;justify-content:space-between;gap:8px;color:#bffef6;font-size:.68rem;font-weight:900;line-height:1.2;}
  .jk-codex-history-list{display:grid;gap:6px;min-width:0;}
  .jk-codex-history-empty{border:1px dashed rgba(167,139,250,.22);border-radius:8px;padding:8px;color:#9fb5c7;font-size:.68rem;font-weight:800;text-align:center;}
  .jk-codex-history-item{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;align-items:center;border:1px solid rgba(167,139,250,.18);border-radius:8px;background:rgba(15,23,42,.62);padding:7px;}
  .jk-codex-history-item.is-active{border-color:rgba(20,184,166,.5);background:rgba(20,184,166,.1);}
  .jk-codex-history-title{min-width:0;color:#f8fbff;font-size:.7rem;font-weight:900;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-codex-history-meta{min-width:0;color:#9fb5c7;font-size:.61rem;font-weight:800;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-codex-history-actions{display:flex;gap:5px;align-items:center;}
  .jk-codex-history-actions button{min-width:28px;height:26px;border:1px solid rgba(167,139,250,.26);border-radius:8px;background:rgba(31,41,55,.82);color:#eef2ff;font-size:.68rem;font-weight:900;cursor:pointer;padding:0 7px;}
  .jk-codex-history-actions button.danger{border-color:rgba(248,113,113,.42);background:rgba(127,29,29,.34);color:#ffd7d7;}
  #jk-codex-messages{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;gap:9px;padding:12px;overflow-y:auto;overflow-x:hidden;background:#07131f;scrollbar-width:thin;scrollbar-color:rgba(167,139,250,.12) transparent;}
  #jk-codex-messages::-webkit-scrollbar{width:5px;height:5px;}
  #jk-codex-messages::-webkit-scrollbar-track{background:transparent;}
  #jk-codex-messages::-webkit-scrollbar-thumb{background:rgba(167,139,250,.08);border-radius:999px;}
  #jk-codex-messages:hover::-webkit-scrollbar-thumb{background:rgba(167,139,250,.2);}
  .jk-codex-msg{max-width:88%;min-width:0;border-radius:15px;padding:9px 11px;font-size:.8rem;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;border:1px solid transparent;}
  .jk-codex-msg.user{align-self:flex-end;background:linear-gradient(145deg,#2563eb,#7c3aed);color:#fff;border-bottom-right-radius:4px;}
  .jk-codex-msg.assistant{align-self:flex-start;background:rgba(30,41,59,.78);border-color:rgba(167,139,250,.28);color:#eef2ff;border-bottom-left-radius:4px;}
  .jk-codex-msg.status{align-self:center;max-width:100%;background:rgba(20,184,166,.11);border-color:rgba(20,184,166,.24);color:#bffef6;font-size:.72rem;text-align:center;}
  .jk-codex-msg.codex-alert{align-self:stretch;max-width:100%;width:100%;border-left:4px solid #f59e0b;background:linear-gradient(135deg,rgba(30,41,59,.9),rgba(41,28,12,.78));box-shadow:0 14px 34px rgba(2,6,23,.24);}
  .jk-codex-msg.codex-alert h2{font-size:.82rem;line-height:1.25;margin:0 0 6px;color:#fde68a;}
  .jk-codex-msg.codex-alert strong{color:#fff7d6;}
  .jk-codex-msg.codex-report{align-self:stretch;max-width:100%;width:100%;display:block;}
  .jk-codex-msg.codex-report h1{font-size:.98rem;line-height:1.22;margin:0 0 7px;color:#f8fbff;}
  .jk-codex-msg.codex-report h2{font-size:.86rem;line-height:1.22;margin:12px 0 6px;color:#bffef6;}
  .jk-codex-msg.codex-report h3{font-size:.78rem;line-height:1.25;margin:10px 0 5px;color:#dcd7ff;}
  .jk-codex-msg.codex-report ul,.jk-codex-msg.codex-report ol{margin:5px 0 8px;padding-left:18px;}
  .jk-codex-msg.codex-report li{margin:4px 0;}
  .jk-codex-msg.codex-report table{display:block;max-width:100%;overflow:auto;border-collapse:collapse;margin:7px 0;border:1px solid rgba(167,139,250,.22);border-radius:8px;}
  .jk-codex-msg.codex-report th,.jk-codex-msg.codex-report td{border:1px solid rgba(167,139,250,.18);padding:5px 6px;vertical-align:top;font-size:.68rem;line-height:1.28;}
  .jk-codex-msg.codex-report th{background:rgba(20,184,166,.14);color:#eafffb;font-weight:900;}
  .jk-codex-report-downloads{border-top:1px solid rgba(167,139,250,.2);padding-top:8px;justify-content:flex-start;}
  .jk-codex-msg pre{max-width:100%;overflow:auto;white-space:pre;background:rgba(2,6,23,.7);border:1px solid rgba(167,139,250,.25);border-radius:8px;padding:8px;font-size:.74rem;}
  .jk-codex-msg code{background:rgba(2,6,23,.52);border:1px solid rgba(167,139,250,.22);border-radius:5px;padding:1px 5px;font-size:.75rem;}
  .jk-codex-msg a{color:#99f6e4;text-decoration:underline;text-underline-offset:2px;}
  .jk-codex-msg-actions{display:flex;gap:7px;align-items:center;justify-content:flex-end;flex-wrap:wrap;margin-top:8px;}
  .jk-codex-action-card{display:grid;gap:7px;min-width:0;border:1px solid rgba(20,184,166,.32);border-radius:10px;background:rgba(8,47,73,.38);padding:9px;color:#e6fffb;}
  .jk-codex-action-card.is-approved{border-color:rgba(74,222,128,.44);background:rgba(22,101,52,.2);}
  .jk-codex-action-card.is-canceled{opacity:.72;border-color:rgba(148,163,184,.25);background:rgba(30,41,59,.48);}
  .jk-codex-action-title{font-size:.82rem;font-weight:900;line-height:1.25;color:#fff;overflow-wrap:anywhere;}
  .jk-codex-action-meta{font-size:.64rem;font-weight:900;line-height:1.25;color:#9ee8df;overflow-wrap:anywhere;}
  .jk-codex-action-risk{border:1px solid rgba(251,191,36,.3);border-radius:8px;background:rgba(120,65,15,.2);padding:6px 7px;color:#fff0c2;font-size:.68rem;font-weight:900;line-height:1.3;}
  .jk-codex-action-detail{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;color:#d8f7ff;font-size:.7rem;font-weight:800;line-height:1.35;}
  .jk-codex-action-logs{max-height:76px;overflow:auto;white-space:pre-wrap;border:1px solid rgba(167,139,250,.2);border-radius:8px;background:rgba(2,6,23,.44);padding:6px;color:#c7d2fe;font-size:.64rem;font-weight:800;line-height:1.35;}
  .jk-codex-action-error{white-space:pre-wrap;overflow-wrap:anywhere;color:#ffd1d1;font-size:.68rem;font-weight:900;line-height:1.35;}
  #jk-codex-suggestions{display:grid;gap:6px;max-height:112px;overflow:auto;padding:8px 10px;border-top:1px solid rgba(20,184,166,.16);background:#07131f;scrollbar-width:none;-ms-overflow-style:none;}
  #jk-codex-suggestions[hidden]{display:none!important;}
  #jk-codex-suggestions::-webkit-scrollbar{width:0;height:0;display:none;}
  .jk-codex-suggestion{display:grid;gap:2px;border:1px solid rgba(20,184,166,.22);border-radius:9px;background:rgba(20,184,166,.08);padding:7px 8px;color:#dffefa;font-size:.68rem;line-height:1.28;}
  .jk-codex-suggestion.warning{border-color:rgba(251,191,36,.36);background:rgba(120,65,15,.18);color:#fff2c4;}
  .jk-codex-suggestion.ok{border-color:rgba(74,222,128,.28);background:rgba(22,101,52,.16);}
  .jk-codex-suggestion strong{font-size:.72rem;color:#f8fbff;line-height:1.2;}
  .jk-codex-suggestion small{color:rgba(226,246,255,.78);font-size:.62rem;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-codex-suggestion-actions{display:flex;gap:6px;align-items:center;justify-content:flex-end;margin-top:3px;}
  .jk-codex-suggestion-actions a{color:#99f6e4;font-weight:900;text-decoration:none;}
  #jk-codex-approval{display:none;border-top:1px solid rgba(251,191,36,.22);border-bottom:1px solid rgba(251,191,36,.16);padding:10px 12px;background:rgba(120,65,15,.2);color:#ffedc2;font-size:.74rem;font-weight:800;line-height:1.35;}
  #jk-codex-approval.ativo{display:grid;gap:8px;}
  #jk-codex-approval-actions{display:flex;gap:8px;flex-wrap:wrap;}
  #jk-codex-runtime{display:none!important;}
  #jk-codex-runtime[hidden]{display:none!important;}
  .jk-codex-runtime-head{display:flex;align-items:center;justify-content:flex-start;min-width:0;width:100%;}
  #jk-codex-live-status{display:block;flex:1 1 auto;min-width:0;text-align:left;color:#bffef6;font-size:.68rem;font-weight:800;line-height:30px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  #jk-codex-runtime .jk-codex-runtime-grid,#jk-codex-runtime .jk-codex-live-block,#jk-codex-runtime #jk-codex-log-list{display:none!important;}
  .jk-codex-runtime-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;}
  .jk-codex-runtime-card{min-width:0;border:1px solid rgba(167,139,250,.22);border-radius:8px;background:rgba(15,23,42,.78);padding:7px;}
  .jk-codex-runtime-card span{display:block;color:#9fb5c7;font-size:.62rem;font-weight:900;line-height:1.2;text-transform:uppercase;}
  .jk-codex-runtime-card strong{display:block;margin-top:3px;color:#f8fbff;font-size:.78rem;font-weight:900;line-height:1.25;overflow-wrap:anywhere;}
  .jk-codex-runtime-card small{display:block;margin-top:2px;color:#a7f3d0;font-size:.62rem;font-weight:800;line-height:1.25;overflow-wrap:anywhere;}
  .jk-codex-live-block{display:grid;gap:4px;border:1px solid rgba(20,184,166,.2);border-radius:8px;background:rgba(8,47,73,.36);padding:7px;color:#e0f2fe;font-size:.68rem;line-height:1.35;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;}
  .jk-codex-live-block strong{color:#bffef6;font-size:.66rem;font-weight:900;text-transform:uppercase;}
  #jk-codex-log-list{display:grid;gap:5px;}
  .jk-codex-log-row{display:flex;gap:6px;align-items:flex-start;min-width:0;color:#dbeafe;font-size:.66rem;line-height:1.3;}
  .jk-codex-log-row small{flex:0 0 auto;min-width:54px;color:#99f6e4;font-weight:900;text-transform:uppercase;}
  .jk-codex-log-row span{min-width:0;overflow-wrap:anywhere;word-break:break-word;}
  .jk-codex-btn{border:1px solid rgba(167,139,250,.34);border-radius:10px;background:rgba(31,41,55,.84);color:#eef2ff;min-height:34px;padding:0 10px;font-size:.74rem;font-weight:900;cursor:pointer;}
  .jk-codex-btn.primary{border:0;background:linear-gradient(145deg,#14b8a6,#60a5fa);color:#041722;}
  .jk-codex-btn.danger{border-color:rgba(248,113,113,.46);background:rgba(127,29,29,.42);color:#ffd7d7;}
  .jk-codex-btn:disabled{opacity:.55;cursor:not-allowed;}
  #jk-codex-compose{display:grid;gap:8px;padding:10px;border-top:1px solid rgba(167,139,250,.2);background:#0c1b2b;}
  #jk-codex-toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;min-width:0;}
  .jk-codex-tool,.jk-codex-select{min-height:30px;border:1px solid rgba(167,139,250,.3);border-radius:10px;background:rgba(17,24,39,.84);color:#eef2ff;font:inherit;font-size:.7rem;font-weight:900;outline:none;}
  .jk-codex-tool{display:inline-flex;align-items:center;justify-content:center;padding:0 9px;cursor:pointer;}
  .jk-codex-tool:hover,.jk-codex-tool.is-active,.jk-codex-select-wrap:hover,.jk-codex-select-wrap:focus-within,.jk-codex-select:focus{border-color:rgba(20,184,166,.66);background:rgba(20,184,166,.14);}
  .jk-codex-tool.icon{flex:0 0 32px;width:32px;padding:0;font-size:1rem;}
  .jk-codex-select{flex:1 1 118px;min-width:118px;max-width:100%;padding:0 8px;cursor:pointer;}
  .jk-codex-select-wrap{position:relative;flex:0 0 32px;width:32px;height:30px;display:inline-flex;align-items:center;justify-content:center;border:1px solid rgba(167,139,250,.3);border-radius:10px;background:rgba(17,24,39,.84);color:#eef2ff;font:inherit;font-size:.7rem;font-weight:900;outline:none;cursor:pointer;}
  .jk-codex-select-wrap .jk-codex-select{position:absolute;inset:0;opacity:0;width:100%;height:100%;min-width:0;padding:0;margin:0;cursor:pointer;}
  .jk-codex-select-icon{line-height:1;font-size:.95rem;pointer-events:none;user-select:none;}
  .jk-codex-select option{background:#111827;color:#f8fbff;font-weight:800;}
  .jk-codex-select option:checked{background:#164e63;color:#f8fbff;}
  .jk-codex-select option:hover{background:#1f2937;color:#ffffff;}
  #jk-codex-add-menu[hidden],#jk-codex-path-row[hidden],#jk-codex-goal-row[hidden]{display:none!important;}
  #jk-codex-add-menu{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px;}
  #jk-codex-add-menu .jk-codex-tool{width:100%;min-width:0;padding:0 7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  #jk-codex-path-row,#jk-codex-goal-row{display:flex;gap:6px;align-items:center;min-width:0;}
  #jk-codex-path-input,#jk-codex-goal-input{flex:1 1 auto;min-width:0;min-height:30px;border:1px solid rgba(167,139,250,.26);border-radius:10px;background:#07131f;color:#f8fbff;font:inherit;font-size:.72rem;padding:0 9px;outline:none;}
  #jk-codex-path-input:focus,#jk-codex-goal-input:focus{border-color:rgba(20,184,166,.7);box-shadow:0 0 0 2px rgba(20,184,166,.12);}
  #jk-codex-path-chips{display:flex;gap:5px;flex-wrap:wrap;min-height:0;}
  .jk-codex-chip{display:inline-flex;align-items:center;gap:5px;max-width:100%;border:1px solid rgba(20,184,166,.28);border-radius:999px;background:rgba(20,184,166,.1);color:#cffff9;font-size:.67rem;font-weight:800;padding:4px 7px;}
  #jk-codex-attachment-chips{display:flex;gap:5px;flex-wrap:wrap;min-height:0;}
  .jk-codex-chip.uploading{border-color:rgba(250,204,21,.45);background:rgba(250,204,21,.12);color:#fff6bf;}
  .jk-codex-chip.error{border-color:rgba(248,113,113,.5);background:rgba(127,29,29,.36);color:#ffd6d6;}
  .jk-codex-chip span{min-width:0;max-width:250px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-codex-chip button{border:0;background:transparent;color:#ffd1d1;cursor:pointer;font:inherit;font-size:.82rem;line-height:1;padding:0;}
  #jk-codex-input{width:100%;min-height:74px;max-height:180px;resize:vertical;border:1px solid rgba(167,139,250,.28);border-radius:12px;background:#07131f;color:#f8fbff;font:inherit;font-size:.82rem;line-height:1.4;padding:9px 10px;outline:none;overflow-wrap:anywhere;word-break:break-word;}
  #jk-codex-input:focus{border-color:rgba(20,184,166,.72);box-shadow:0 0 0 2px rgba(20,184,166,.13);}
  #jk-codex-compose.is-dragover{box-shadow:inset 0 0 0 2px rgba(20,184,166,.62);background:#102536;}
  #jk-codex-actions{display:flex;gap:8px;align-items:center;}
  #jk-codex-actions .jk-codex-btn{flex:1 1 0;}
  #jk-ia-resizer,#jk-codex-resizer{position:absolute;left:-8px;top:0;bottom:0;width:18px;cursor:ew-resize;touch-action:none;z-index:5;}
  #jk-ia-resizer::before{content:"";position:absolute;left:4px;top:50%;width:11px;height:52px;transform:translateY(-50%);border:1px solid rgba(120,227,212,.34);border-right:0;border-radius:999px 0 0 999px;background:rgba(35,142,165,.42);box-shadow:0 0 14px rgba(120,227,212,.18);transition:background .15s ease,border-color .15s ease,box-shadow .15s ease;}
  #jk-ia-resizer::after{content:"";position:absolute;left:9px;top:50%;width:2px;height:26px;transform:translateY(-50%);border-radius:999px;background:rgba(224,255,250,.66);box-shadow:-3px 0 0 rgba(224,255,250,.32),3px 0 0 rgba(224,255,250,.32);transition:background .15s ease,box-shadow .15s ease;}
  #jk-ia-resizer:hover::before,#jk-ia-resizer:focus-visible::before,body.jk-ia-resizing #jk-ia-resizer::before{background:rgba(36,161,160,.66);border-color:rgba(120,227,212,.72);box-shadow:0 0 18px rgba(120,227,212,.34);}
  #jk-ia-resizer:hover::after,#jk-ia-resizer:focus-visible::after,body.jk-ia-resizing #jk-ia-resizer::after{background:#eafffb;box-shadow:-3px 0 0 rgba(234,255,251,.52),3px 0 0 rgba(234,255,251,.52),0 0 12px rgba(120,227,212,.42);}
  #jk-codex-resizer::before{content:"";position:absolute;left:4px;top:50%;width:11px;height:52px;transform:translateY(-50%);border:1px solid rgba(167,139,250,.3);border-right:0;border-radius:999px 0 0 999px;background:rgba(49,46,129,.28);box-shadow:0 0 14px rgba(167,139,250,.12);transition:background .15s ease,border-color .15s ease,box-shadow .15s ease;}
  #jk-codex-resizer::after{content:"";position:absolute;left:9px;top:50%;width:2px;height:26px;transform:translateY(-50%);border-radius:999px;background:rgba(224,231,255,.48);box-shadow:-3px 0 0 rgba(224,231,255,.22),3px 0 0 rgba(224,231,255,.22);transition:background .15s ease,box-shadow .15s ease;}
  #jk-codex-resizer:hover::before,#jk-codex-resizer:focus-visible::before,body.jk-codex-resizing #jk-codex-resizer::before{background:rgba(91,70,160,.48);border-color:rgba(167,139,250,.66);box-shadow:0 0 18px rgba(167,139,250,.28);}
  #jk-codex-resizer:hover::after,#jk-codex-resizer:focus-visible::after,body.jk-codex-resizing #jk-codex-resizer::after{background:#f2efff;box-shadow:-3px 0 0 rgba(242,239,255,.42),3px 0 0 rgba(242,239,255,.42),0 0 12px rgba(167,139,250,.35);}
  body.jk-ia-resizing,body.jk-codex-resizing{cursor:ew-resize;user-select:none;}
  #jk-ia-panel-header{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid rgba(106,225,203,.18);background:rgba(4,30,48,.7);}
  #jk-ia-panel-header h3{flex:1 1 auto;margin:0;color:#8ee9de;font-size:.96rem;font-weight:700;}
  #jk-ia-panel-header{flex-wrap:nowrap;overflow:hidden;}
  #jk-ia-panel-header h3{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  #jk-ia-model-sel{
    width:132px !important;
    min-width:132px !important;
    max-width:132px !important;
    flex:0 0 132px !important;
  }
  .jk-ia-hbtn{border:0;background:transparent;color:#8ee9de;cursor:pointer;font-size:1.05rem;padding:0 12px;border-radius:8px;line-height:1;min-height:36px;min-width:40px;height:36px;display:flex;align-items:center;justify-content:center;pointer-events:auto;touch-action:manipulation;user-select:none;-webkit-user-select:none;}
  .jk-ia-hbtn:hover{background:rgba(19,196,160,.18);transform:scale(1.05);}
  #jk-ia-convs-panel{display:none;flex-direction:column;gap:6px;padding:10px;overflow-y:auto;flex:1 1 0;min-height:0;background:rgba(4,20,34,.7);}
  #jk-ia-convs-panel.ativo{display:flex;}
  .jk-ia-conv-item{display:flex;align-items:center;gap:8px;padding:9px 10px;border:1px solid rgba(106,225,203,.22);border-radius:10px;cursor:pointer;background:rgba(15,60,80,.3);}
  .jk-ia-conv-item:hover{background:rgba(36,161,160,.2);border-color:rgba(106,225,203,.5);}
  .jk-ia-conv-item.ativa{border-color:#13c4a0;background:rgba(19,196,160,.12);}
  .jk-ia-conv-info{flex:1 1 auto;overflow:hidden;}
  .jk-ia-conv-data{color:#8ee9de;font-size:.7rem;}
  .jk-ia-conv-prev{color:#cde;font-size:.76rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-ia-conv-del{border:0;background:transparent;color:#f87171;cursor:pointer;font-size:.85rem;padding:2px 6px;border-radius:6px;opacity:.7;}
  .jk-ia-conv-del:hover{opacity:1;background:rgba(248,113,113,.15);}
  #jk-ia-chat-wrap{display:flex;flex-direction:column;flex:1 1 0;min-width:0;min-height:0;overflow:hidden;}
  #jk-ia-status{flex:0 0 auto;color:#bdeee7;font-size:.72rem;padding:6px 12px 2px;background:transparent;}
  #jk-ia-msgs{flex:1 1 0;min-width:0;min-height:0;overflow-y:auto;overflow-x:hidden;display:flex;flex-direction:column;gap:10px;padding:10px;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;scrollbar-width:none;
    background:rgba(2,18,30,.4);}
  #jk-ia-msgs::-webkit-scrollbar{display:none;}
  .jk-ia-msg{max-width:87%;min-width:0;overflow:visible;border-radius:16px;padding:9px 12px;font-size:.83rem;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;border:1px solid transparent;}
  .jk-ia-msg.user{align-self:flex-end;color:#fff;background:linear-gradient(165deg,#1888ff,#0f65d8);border-bottom-right-radius:4px;}
  .jk-ia-msg.assistant{align-self:flex-start;color:#e8fffb;background:linear-gradient(165deg,rgba(30,83,123,.7),rgba(20,104,90,.55));border-color:rgba(120,227,212,.34);border-bottom-left-radius:4px;}
  .jk-ia-msg.assistant strong{color:#fff;}
  .jk-ia-msg.assistant a{color:#8ee9de;text-decoration:underline;text-underline-offset:2px;overflow-wrap:anywhere;word-break:break-word;}
  .jk-ia-msg.assistant a:hover{color:#c8fff8;}
  .jk-ia-msg.assistant .jk-ia-img-link{display:inline-block;max-width:100%;margin:8px 0;}
  .jk-ia-msg.assistant .jk-ia-img{display:block;max-width:min(220px,100%);max-height:220px;object-fit:contain;border-radius:10px;border:1px solid rgba(120,227,212,.32);background:rgba(4,26,35,.88);padding:4px;}
  .jk-ia-msg.assistant .jk-ia-img-error{border-color:rgba(255,138,128,.55);}
  .jk-ia-msg.assistant .jk-ia-img-fallback{display:none;}
  .jk-ia-msg.assistant .jk-ia-img-link.is-broken{display:inline-flex;align-items:center;max-width:100%;margin:0 2px;padding:0;border:0;background:transparent;color:#8ee9de;text-decoration:underline;text-underline-offset:2px;}
  .jk-ia-msg.assistant .jk-ia-img-link.is-broken .jk-ia-img{display:none;}
  .jk-ia-msg.assistant .jk-ia-img-link.is-broken .jk-ia-img-fallback{display:inline;overflow-wrap:anywhere;word-break:break-word;}
  .jk-ia-msg.assistant .jk-ia-file-card{display:flex;align-items:center;gap:10px;max-width:100%;margin:8px 0;padding:9px 10px;border:1px solid rgba(120,227,212,.3);border-radius:10px;background:rgba(4,26,35,.72);color:#e8fffb;text-decoration:none;}
  .jk-ia-msg.assistant .jk-ia-file-card:hover{border-color:rgba(120,227,212,.62);background:rgba(7,43,57,.86);}
  .jk-ia-msg.assistant .jk-ia-file-icon{flex:0 0 34px;width:34px;height:34px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;background:rgba(83,212,183,.16);border:1px solid rgba(120,227,212,.24);font-size:.72rem;font-weight:900;color:#8ee9de;}
  .jk-ia-msg.assistant .jk-ia-file-info{min-width:0;display:grid;gap:2px;}
  .jk-ia-msg.assistant .jk-ia-file-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:800;color:#fff;}
  .jk-ia-msg.assistant .jk-ia-file-action{font-size:.74rem;color:#9ee8df;}
  .jk-ia-msg.assistant code{background:rgba(5,28,47,.5);border:1px solid rgba(120,227,212,.3);border-radius:5px;padding:1px 5px;font-size:.77rem;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;}
  .jk-ia-msg.assistant h1,.jk-ia-msg.assistant h2,.jk-ia-msg.assistant h3,.jk-ia-msg.assistant h4{margin:10px 0 6px;line-height:1.25;color:#fff;}
  .jk-ia-msg.assistant h1{font-size:1.04rem;}
  .jk-ia-msg.assistant h2{font-size:.98rem;}
  .jk-ia-msg.assistant h3{font-size:.93rem;}
  .jk-ia-msg.assistant h4{font-size:.89rem;}
  .jk-ia-msg.assistant p{margin:6px 0;max-width:100%;overflow-wrap:anywhere;word-break:break-word;}
  .jk-ia-msg.assistant ul,.jk-ia-msg.assistant ol{margin:6px 0 8px 18px;padding:0;}
  .jk-ia-msg.assistant li{margin:3px 0;overflow-wrap:anywhere;word-break:break-word;}
  .jk-ia-msg.assistant .jk-ia-task-list{list-style:none;margin-left:0;}
  .jk-ia-msg.assistant .jk-ia-task-item{display:flex;align-items:flex-start;gap:7px;}
  .jk-ia-msg.assistant .jk-ia-task-item input{margin-top:3px;accent-color:#53d4b7;}
  .jk-ia-msg.assistant blockquote{margin:8px 0;padding:6px 10px;border-left:3px solid rgba(120,227,212,.6);background:rgba(6,36,51,.45);border-radius:6px;}
  .jk-ia-msg.assistant pre{max-width:100%;margin:8px 0;padding:8px 10px;border-radius:8px;background:rgba(5,24,35,.92);border:1px solid rgba(120,227,212,.25);overflow-x:auto;white-space:pre;font-size:.75rem;}
  .jk-ia-msg.assistant hr{border:0;border-top:1px solid rgba(120,227,212,.25);margin:10px 0;}
  .jk-ia-msg.assistant table{width:100%;border-collapse:collapse;margin:8px 0;font-size:.76rem;background:rgba(6,36,51,.45);border:1px solid rgba(120,227,212,.25);border-radius:8px;overflow:hidden;display:block;overflow-x:auto;}
  .jk-ia-msg.assistant thead tr{background:rgba(15,70,85,.45);}
  .jk-ia-msg.assistant th,.jk-ia-msg.assistant td{border:1px solid rgba(120,227,212,.2);padding:6px 8px;text-align:left;white-space:nowrap;}
  .jk-ia-approval-card{display:grid;gap:8px;}
  .jk-ia-approval-title{font-weight:900;color:#fff;}
  .jk-ia-approval-meta{font-size:.73rem;color:#9ee8df;line-height:1.35;}
  .jk-ia-approval-question,.jk-ia-approval-answer{min-width:0;padding:8px;border-radius:8px;background:rgba(4,26,35,.56);border:1px solid rgba(120,227,212,.18);overflow-wrap:anywhere;word-break:break-word;}
  .jk-ia-approval-conversation{display:grid;gap:7px;max-height:280px;overflow:auto;padding:8px;border-radius:8px;background:rgba(4,26,35,.38);border:1px solid rgba(120,227,212,.18);scrollbar-width:none;-ms-overflow-style:none;}
  .jk-ia-approval-conversation::-webkit-scrollbar{width:0;height:0;display:none;}
  .jk-ia-approval-message{min-width:0;padding:7px 8px;border-radius:9px;border:1px solid rgba(120,227,212,.14);background:rgba(11,48,64,.5);overflow-wrap:anywhere;word-break:break-word;}
  .jk-ia-approval-message.seller{background:rgba(16,95,76,.34);margin-left:18px;}
  .jk-ia-approval-message.buyer{background:rgba(23,47,70,.55);margin-right:18px;}
  .jk-ia-approval-message-head{font-size:.67rem;text-transform:uppercase;letter-spacing:.02em;color:#9ee8df;font-weight:900;margin-bottom:4px;}
  .jk-ia-approval-attachments{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px;}
  .jk-ia-approval-attachment-img{display:block;width:76px;height:76px;object-fit:cover;border-radius:9px;border:1px solid rgba(120,227,212,.28);background:rgba(2,19,29,.75);}
  .jk-ia-approval-file{display:inline-flex;align-items:center;min-height:30px;padding:5px 8px;border-radius:8px;border:1px solid rgba(120,227,212,.26);color:#e8fffb;text-decoration:none;background:rgba(2,19,29,.55);font-size:.72rem;}
  .jk-ia-approval-edit{width:100%;min-height:130px;resize:vertical;border-radius:8px;border:1px solid rgba(120,227,212,.26);background:rgba(3,22,32,.82);color:#e8fffb;padding:8px;font:inherit;font-size:.78rem;line-height:1.45;outline:none;scrollbar-width:none;-ms-overflow-style:none;overflow-wrap:anywhere;word-break:break-word;}
  .jk-ia-approval-edit::-webkit-scrollbar{width:0;height:0;display:none;}
  .jk-ia-approval-edit:focus{border-color:#53d4b7;box-shadow:0 0 0 2px rgba(83,212,183,.16);}
  .jk-ia-approval-label{font-size:.7rem;text-transform:uppercase;letter-spacing:.03em;color:#8ee9de;font-weight:900;margin-bottom:4px;}
  .jk-ia-approval-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:2px;}
  .jk-ia-approval-btn{border:1px solid rgba(120,227,212,.36);border-radius:8px;background:rgba(35,142,165,.22);color:#e8fffb;font-weight:900;font-size:.75rem;min-height:34px;padding:0 10px;cursor:pointer;}
  .jk-ia-approval-btn.primary{background:linear-gradient(165deg,#53d4b7,#33a7d7);border:0;color:#03272f;}
  .jk-ia-approval-btn.danger{border-color:rgba(248,113,113,.45);background:rgba(248,113,113,.16);color:#ffd7d7;}
  .jk-ia-approval-btn:disabled{cursor:not-allowed;opacity:.56;}
  .jk-ia-approval-context-status{align-self:center;flex:1 1 100%;min-height:16px;color:#9ee8df;font-size:.7rem;font-weight:800;line-height:1.35;}
  .jk-ia-approval-status-btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;width:100%;min-height:34px;border-radius:8px;border:1px solid rgba(120,227,212,.34);background:rgba(21,94,79,.24);color:#c9fff5;font-weight:900;font-size:.75rem;padding:0 10px;cursor:default;}
  .jk-ia-approval-status-btn.approved{border-color:rgba(83,212,183,.56);background:rgba(21,128,101,.32);color:#d8fff8;}
  .jk-ia-approval-status-btn.rejected{border-color:rgba(248,113,113,.45);background:rgba(248,113,113,.14);color:#ffd7d7;}
  .jk-ia-approval-status-btn.error{border-color:rgba(251,191,36,.48);background:rgba(251,191,36,.13);color:#fff0bd;}
  .jk-ia-approval-resolved{font-size:.72rem;font-weight:900;color:#9ee8df;border:1px solid rgba(120,227,212,.22);background:rgba(21,94,79,.25);border-radius:8px;padding:7px 8px;}
  .jk-ia-approval-card[data-resolved="1"] .jk-ia-approval-edit{opacity:.68;}
  .jk-ia-msg.loading{color:#b8c7d9;font-style:italic;}
  #jk-ia-anexos{display:flex;flex:0 0 auto;flex-wrap:wrap;gap:5px;padding:0 10px;}
  .jk-ia-anx-item{display:inline-flex;align-items:center;gap:5px;border:1px solid rgba(120,227,212,.3);
    background:rgba(25,120,133,.2);border-radius:999px;color:#dbfffb;font-size:.7rem;padding:3px 8px;}
  .jk-ia-anx-item span{max-width:150px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-ia-anx-del{border:0;background:transparent;color:#ffd2d2;cursor:pointer;font-size:.85rem;padding:0;line-height:1;}
  #jk-ia-input-row{display:flex;flex:0 0 auto;align-items:flex-end;gap:7px;min-width:0;border:1px solid rgba(120,227,212,.26);border-radius:16px;
    background:rgba(4,26,35,.9);padding:7px 8px;margin:6px 10px 12px;}
  #jk-ia-input{flex:1 1 auto;resize:none;min-height:40px;max-height:130px;border-radius:11px;border:0;
    min-width:0;background:transparent;color:#e5e5e5;font:inherit;font-size:.81rem;padding:8px 6px;outline:none;overflow-wrap:anywhere;word-break:break-word;}
  .jk-ia-ibtn{flex:0 0 44px;width:44px;height:44px;border-radius:999px;border:1px solid rgba(120,227,212,.36);
    background:rgba(35,142,165,.22);color:#dbfffb;font-size:1.05rem;cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center;}
  .jk-ia-ibtn:hover{border-color:rgba(120,227,212,.7);background:rgba(36,161,160,.3);transform:scale(1.08);}
  #jk-ia-send{background:linear-gradient(165deg,#53d4b7,#33a7d7);border:0;color:#03272f;font-weight:900;flex:0 0 44px;width:44px;height:44px;font-size:1.1rem;display:flex;align-items:center;justify-content:center;}
  `;

  /* ── HTML do widget ── */
  const HTML = `
  <div id="jk-left-sidebar-hotspot" aria-label="Menu lateral esquerdo" tabindex="0">
    <nav id="jk-left-sidebar-menu" aria-label="Modulos do sistema"></nav>
  </div>
  <div id="jk-right-sidebar-hotspot" aria-label="Menu lateral direito" tabindex="0">
    <div id="jk-right-sidebar-menu" role="toolbar" aria-label="Atalhos laterais">
      <button id="jk-ia-fab" class="jk-right-sidebar-icon" title="Assistente IA" aria-label="Abrir assistente IA">&#129302;</button>
      <button id="jk-codex-fab" class="jk-right-sidebar-icon is-hidden" title="João Pretinho" aria-label="Abrir João Pretinho"><img class="jk-codex-avatar" src="/assets/joao-pretinho-icon.png?v=20260623-joao-pretinho" alt=""></button>
      <button id="jk-msg-fab" class="jk-right-sidebar-icon" title="Mensagens" aria-label="Abrir mensagens">&#128172;<span id="jk-msg-badge" aria-label="Mensagens nao lidas"></span></button>
    </div>
  </div>
  <aside id="jk-ia-panel" role="complementary" aria-label="Assistente IA">
    <div id="jk-ia-resizer" role="separator" aria-orientation="vertical" aria-label="Redimensionar assistente IA" tabindex="0"></div>
    <div id="jk-ia-panel-header">
      <h3>🤖 Assistente IA</h3>
      <select id="jk-ia-model-sel" title="Modelo de IA" style="display:none;background:#0b3040;border:1px solid rgba(120,227,212,.3);color:#8ee9de;font-size:.68rem;border-radius:8px;padding:6px 8px;cursor:pointer;max-width:90px;min-height:36px;">
        <optgroup label="OpenAI">
          <option value="gpt-5.4-nano">Nano</option>
          <option value="gpt-5.4-mini">Mini</option>
          <option value="gpt-5.4">GPT-5.4</option>
          <option value="gpt-5.5">GPT-5.5</option>
        </optgroup>
        <optgroup label="DeepSeek">
          <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
          <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
        </optgroup>
        <optgroup label="Gemini API">
          <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
          <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
        </optgroup>
        <optgroup label="Vertex AI (Google Cloud)">
          <option value="vertex:gemini-2.5-flash">Vertex 2.5 Flash</option>
          <option value="vertex:gemini-2.5-pro">Vertex 2.5 Pro</option>
          <option value="vertex:gemini-2.5-flash-lite">Vertex 2.5 Flash-Lite</option>
          <option value="vertex:gemini-2.0-flash">Vertex 2.0 Flash</option>
        </optgroup>
      </select>
      <button class="jk-ia-hbtn" id="jk-ia-btn-historico" title="Ver conversas">🗂</button>
      <button class="jk-ia-hbtn" id="jk-ia-btn-nova" title="Nova conversa">✏️</button>
      <button class="jk-ia-hbtn" id="jk-ia-btn-fechar" title="Fechar">✕</button>
    </div>
    <div id="jk-ia-convs-panel">
      <div style="color:#8ee9de;font-size:.8rem;font-weight:700;padding:4px 0 8px;">Conversas salvas</div>
      <div id="jk-ia-convs-lista"></div>
    </div>
    <div id="jk-ia-chat-wrap">
      <div id="jk-ia-status">Assistente conectado a este módulo.</div>
      <div id="jk-ia-msgs" aria-live="polite"></div>
      <div id="jk-ia-anexos"></div>
      <div id="jk-ia-input-row">
        <button class="jk-ia-ibtn" id="jk-ia-btn-img" title="Enviar foto">🖼</button>
        <button class="jk-ia-ibtn" id="jk-ia-btn-arq" title="Enviar arquivo">📎</button>
        <textarea id="jk-ia-input" placeholder="Pergunte sobre este módulo..." rows="1"></textarea>
        <button class="jk-ia-ibtn" id="jk-ia-send" title="Enviar">↑</button>
      </div>
    </div>
    <input type="file" id="jk-ia-file-img" accept="image/*" multiple style="display:none">
    <input type="file" id="jk-ia-file-arq" accept=".pdf,.txt,.csv,.json,.xml,.md,.log,.xlsx,.xls" multiple style="display:none">
  </aside>
  <aside id="jk-codex-panel" role="complementary" aria-label="João Pretinho">
    <div id="jk-codex-resizer" role="separator" aria-orientation="vertical" aria-label="Redimensionar João Pretinho" tabindex="0"></div>
    <div id="jk-codex-header">
      <div class="jk-codex-icon" aria-hidden="true"><img class="jk-codex-avatar" src="/assets/joao-pretinho-icon.png?v=20260623-joao-pretinho" alt=""></div>
      <div style="flex:1 1 auto;min-width:0;">
        <h3>João Pretinho</h3>
        <span>IA assistente do sistema</span>
      </div>
      <button class="jk-codex-hbtn" id="jk-codex-new" title="Nova conversa">&#9998;</button>
      <button class="jk-codex-hbtn" id="jk-codex-history-toggle" title="Historico">&#128340;</button>
      <button class="jk-codex-hbtn" id="jk-codex-refresh" title="Atualizar status">&#8635;</button>
      <button class="jk-codex-hbtn" id="jk-codex-close" title="Fechar">&times;</button>
    </div>
    <div id="jk-codex-history-panel" aria-live="polite">
      <div class="jk-codex-history-head">
        <span>Historico</span>
        <button class="jk-codex-tool" id="jk-codex-history-refresh" type="button">Atualizar</button>
      </div>
      <div class="jk-codex-history-list" id="jk-codex-history-list"></div>
    </div>
    <div id="jk-codex-messages" aria-live="polite"></div>
    <div id="jk-codex-suggestions" hidden></div>
    <div id="jk-codex-approval">
      <div id="jk-codex-approval-text">Esta tarefa precisa de confirmacao para executar com permissao mutavel.</div>
      <div id="jk-codex-approval-actions">
        <button class="jk-codex-btn primary" id="jk-codex-approve" type="button">Confirmar execucao</button>
        <button class="jk-codex-btn danger" id="jk-codex-cancel" type="button">Cancelar</button>
      </div>
    </div>
    <div id="jk-codex-status-row">
      <div id="jk-codex-runtime" hidden>
        <div class="jk-codex-runtime-head">
          <span id="jk-codex-live-status">Aguardando tarefa.</span>
        </div>
        <div class="jk-codex-runtime-grid">
          <div class="jk-codex-runtime-card">
            <span>Contexto</span>
            <strong id="jk-codex-context-used">-</strong>
            <small id="jk-codex-context-detail">-</small>
          </div>
          <div class="jk-codex-runtime-card">
            <span>Tokens</span>
            <strong id="jk-codex-token-used">-</strong>
            <small id="jk-codex-token-detail">-</small>
          </div>
        </div>
        <div class="jk-codex-live-block" id="jk-codex-reasoning" hidden></div>
        <div class="jk-codex-live-block" id="jk-codex-live-answer" hidden></div>
        <div id="jk-codex-log-list"></div>
      </div>
      <div id="jk-codex-status">João Pretinho disponivel apenas para administrador full.</div>
      <div id="jk-codex-context-pct" title="Contexto usado">Ctx --%</div>
    </div>
    <div id="jk-codex-compose">
      <div id="jk-codex-toolbar">
        <button class="jk-codex-tool icon" id="jk-codex-add-toggle" type="button" title="Adicionar">+</button>
        <label class="jk-codex-select-wrap" title="Acesso">
          <span class="jk-codex-select-icon" aria-hidden="true">&#128274;</span>
          <select class="jk-codex-select" id="jk-codex-access" title="Acesso">
            <option value="read_only">Leitura</option>
            <option value="request">Solicitar aprovacao</option>
            <option value="auto">Aprovar por mim</option>
            <option value="full_access">Acesso completo</option>
          </select>
        </label>
        <label class="jk-codex-select-wrap" title="Modelo">
          <span class="jk-codex-select-icon" aria-hidden="true">&#128187;</span>
          <select class="jk-codex-select" id="jk-codex-model" title="Modelo">
            <option value="gpt-5.5">GPT-5.5</option>
            <option value="gpt-5.4">GPT-5.4</option>
            <option value="gpt-5.4-mini">GPT-5.4 Mini</option>
            <option value="gpt-5.4-nano">GPT-5.4 Nano</option>
          </select>
        </label>
        <label class="jk-codex-select-wrap" title="Raciocinio">
          <span class="jk-codex-select-icon" aria-hidden="true">&#128161;</span>
          <select class="jk-codex-select" id="jk-codex-reasoning" title="Raciocinio">
            <option value="low">Baixa</option>
            <option value="medium">Media</option>
            <option value="high">Alta</option>
            <option value="xhigh">Altissimo</option>
          </select>
        </label>
        <label class="jk-codex-select-wrap" title="Velocidade">
          <span class="jk-codex-select-icon" aria-hidden="true">&#9889;</span>
          <select class="jk-codex-select" id="jk-codex-speed" title="Velocidade">
            <option value="standard">Padrao</option>
            <option value="fast">Rapido</option>
          </select>
        </label>
      </div>
      <div id="jk-codex-add-menu" hidden>
        <button class="jk-codex-tool" id="jk-codex-path-toggle" type="button">Arquivos/pastas</button>
        <button class="jk-codex-tool" id="jk-codex-attach-file" type="button">Arquivo</button>
        <button class="jk-codex-tool" id="jk-codex-goal-toggle" type="button">Meta</button>
        <button class="jk-codex-tool" id="jk-codex-plan-toggle" type="button" aria-pressed="false">Planejamento</button>
        <button class="jk-codex-tool" id="jk-codex-report" type="button">Relatorio</button>
        <button class="jk-codex-tool" id="jk-codex-capabilities" type="button">Capacidades</button>
      </div>
      <div id="jk-codex-path-row" hidden>
        <input id="jk-codex-path-input" type="text" autocomplete="off" placeholder="Caminho de arquivo ou pasta">
        <button class="jk-codex-tool" id="jk-codex-path-add" type="button">Adicionar</button>
      </div>
      <div id="jk-codex-goal-row" hidden>
        <input id="jk-codex-goal-input" type="text" autocomplete="off" placeholder="Meta da tarefa">
      </div>
      <div id="jk-codex-path-chips"></div>
      <div id="jk-codex-attachment-chips"></div>
      <textarea id="jk-codex-input" rows="3" placeholder="Peça uma analise, correcao ou tarefa interna para o João Pretinho..."></textarea>
      <div id="jk-codex-actions">
        <button class="jk-codex-btn primary" id="jk-codex-readonly" type="button" title="Enviar para o João Pretinho">Enviar</button>
      </div>
      <input type="file" id="jk-codex-file-input" multiple style="display:none">
    </div>
  </aside>
  <aside id="jk-msg-panel" role="complementary" aria-label="Mensagens">
    <div id="jk-msg-header">
      <div class="jk-msg-title-wrap">
        <span class="jk-msg-header-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2"/><circle cx="10" cy="7" r="4"/><path d="M21 21v-2a3.5 3.5 0 0 0-2.5-3.35"/><path d="M16 3.2a4 4 0 0 1 0 7.6"/></svg></span>
        <div>
          <h3>Mensagens</h3>
          <span class="jk-msg-header-subtitle">Central de comunicacao</span>
        </div>
      </div>
      <button class="jk-msg-hbtn" id="jk-msg-btn-refresh" title="Atualizar">&#8635;</button>
      <button class="jk-msg-hbtn" id="jk-msg-btn-fechar" title="Fechar">&times;</button>
    </div>
    <div id="jk-msg-body">
      <div class="jk-msg-status" id="jk-msg-status">Aguardando abertura do painel.</div>
      <div id="jk-msg-main-view">
        <section class="jk-msg-section jk-msg-users-section" aria-label="Usuarios">
          <div class="jk-msg-section-head">
            <div class="jk-msg-section-title">Usuarios</div>
            <div class="jk-msg-count-pill" id="jk-msg-contact-count">0 contatos</div>
          </div>
          <div class="jk-msg-list" id="jk-msg-online-list"></div>
        </section>
        <section class="jk-msg-section jk-msg-inbox-section jk-msg-section-empty" aria-label="Mensagens recebidas">
          <div class="jk-msg-section-title">Mensagens recebidas</div>
          <div class="jk-msg-list" id="jk-msg-inbox-list"></div>
        </section>
      </div>
      <div id="jk-msg-chat-header">
        <button id="jk-msg-back" type="button" title="Voltar para usuarios">&#8592;</button>
        <div id="jk-msg-chat-title">Chat</div>
        <button id="jk-msg-video-call" type="button" title="Iniciar videochamada Daily" disabled>&#128249;</button>
      </div>
      <div id="jk-msg-history" aria-live="polite"></div>
      <div id="jk-msg-typing" aria-live="polite"></div>
    </div>
    <div id="jk-msg-compose">
      <div id="jk-msg-selected">Selecione um usuario para enviar mensagem.</div>
      <div id="jk-msg-emoji-panel" aria-label="Escolher emoji"></div>
      <div id="jk-msg-anexos" aria-live="polite"></div>
      <div id="jk-msg-tools" aria-label="Ferramentas da mensagem">
        <div class="jk-msg-input-pill">
          <button class="jk-msg-tool-btn" id="jk-msg-emoji-btn" type="button" title="Emoji"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M8.5 10h.01M15.5 10h.01M8 14.5c1.2 1.2 2.5 1.8 4 1.8s2.8-.6 4-1.8"/></svg></button>
          <textarea id="jk-msg-text" maxlength="2000" placeholder="Mensagem" aria-label="Texto da mensagem"></textarea>
          <button class="jk-msg-tool-btn" id="jk-msg-file-btn" type="button" title="Arquivo"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21.4 11.6l-8.5 8.5a5 5 0 0 1-7.1-7.1l9.2-9.2a3.5 3.5 0 0 1 5 5L10.6 18a2 2 0 0 1-2.8-2.8l8.5-8.5"/></svg></button>
          <button class="jk-msg-tool-btn" id="jk-msg-img-btn" type="button" title="Imagem ou GIF"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="3"/><circle cx="8.5" cy="10" r="1.5"/><path d="M21 16l-5.2-5.2a2 2 0 0 0-2.8 0L5 19"/></svg></button>
          <button class="jk-msg-tool-btn" id="jk-msg-audio-btn" type="button" title="Gravar audio"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></svg></button>
        </div>
        <button id="jk-msg-send" type="button" title="Enviar"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/></svg></button>
      </div>
      <input type="file" id="jk-msg-img-input" accept="image/*,.gif" multiple style="display:none">
      <input type="file" id="jk-msg-file-input" multiple style="display:none">
    </div>
  </aside>
  <div id="jk-msg-call-modal" hidden role="dialog" aria-modal="true" aria-labelledby="jk-msg-call-title">
    <div class="jk-msg-call-dialog">
      <div class="jk-msg-call-avatar" id="jk-msg-call-avatar">?</div>
      <div>
        <h3 id="jk-msg-call-title">Chamada de video</h3>
        <p id="jk-msg-call-subtitle">Usuario chamando.</p>
      </div>
      <div class="jk-msg-call-dialog-actions">
        <button id="jk-msg-call-decline" type="button">Recusar</button>
        <button id="jk-msg-call-answer" type="button">Atender</button>
      </div>
    </div>
  </div>
  <div id="jk-msg-image-modal" hidden role="dialog" aria-modal="true" aria-labelledby="jk-msg-image-modal-title">
    <div class="jk-msg-image-modal-bar">
      <div class="jk-msg-image-modal-title" id="jk-msg-image-modal-title">Imagem</div>
      <button class="jk-msg-image-modal-btn" id="jk-msg-image-modal-copy" type="button">Copiar</button>
      <a class="jk-msg-image-modal-btn" id="jk-msg-image-modal-download" href="#" download="imagem" role="button">Baixar</a>
      <button class="jk-msg-image-modal-btn" id="jk-msg-image-modal-close" type="button" aria-label="Fechar">&times;</button>
    </div>
    <div class="jk-msg-image-modal-stage">
      <img id="jk-msg-image-modal-img" alt="">
    </div>
  </div>
  `;

  function _nomeModeloLimpo(item) {
    const nomesFixos = {
      'gpt-5.4-nano': 'Nano',
      'gpt-5.4-mini': 'Mini',
      'gpt-5.4': 'GPT-5.4',
      'gpt-5.5': 'GPT-5.5',
      'deepseek-v4-flash': 'DeepSeek V4 Flash',
      'deepseek-v4-pro': 'DeepSeek V4 Pro',
    };
    const nome = String(item?.name || '').trim();
    if (nomesFixos[nome]) return nomesFixos[nome];
    return String(item?.display_name || nome || '').replace(/[�]/g, '').trim();
  }

  function _substituirGrupoModelos(selectEl, label, modelos) {
    if (!selectEl || !Array.isArray(modelos) || !modelos.length) return;
    let grupo = Array.from(selectEl.querySelectorAll('optgroup')).find(el => el.label === label);
    if (!grupo) {
      grupo = document.createElement('optgroup');
      grupo.label = label;
      selectEl.appendChild(grupo);
    }
    grupo.innerHTML = '';
    modelos.forEach(item => {
      const option = document.createElement('option');
      option.value = item.name;
      option.textContent = _nomeModeloLimpo(item);
      if (item.description) option.title = item.description;
      grupo.appendChild(option);
    });
  }

  async function _carregarModelosRemotos(selectEl, storageKey) {
    if (!selectEl) return;
    const valorSalvo = _usuarioLocalEhAdmin() ? (localStorage.getItem(storageKey) || selectEl.value) : '';
    const urls = ['/api/ia/modelos'];
    if (location.hostname === '127.0.0.1' || location.hostname === 'localhost') {
      urls.push('http://127.0.0.1:8012/api/ia/modelos');
    }

    for (const url of urls) {
      try {
        const resp = await window.__JK_IA_SIDEBAR_FETCH__(url, { method: 'GET', headers: _authHeaders() });
        if (!resp.ok) {
          if (resp.status !== 405) break;
          continue;
        }
        const data = await resp.json().catch(() => null);
        if (!data || !data.success) return;
        const podeEscolher = data.pode_escolher_modelo_chat === true;
        _aplicarPermissaoModeloChat(selectEl, podeEscolher);
        _substituirGrupoModelos(selectEl, 'OpenAI', data.openai || []);
        _substituirGrupoModelos(selectEl, 'DeepSeek', data.deepseek || []);
        _substituirGrupoModelos(selectEl, 'Gemini API', data.gemini || []);
        _substituirGrupoModelos(selectEl, 'Vertex AI (Google Cloud)', data.vertex || []);
        const valores = Array.from(selectEl.options).map(opt => opt.value);
        const modeloSistema = data?.defaults?.chat || data?.defaults?.sistema || '';
        const valorPreferido = podeEscolher && valores.includes(valorSalvo)
          ? valorSalvo
          : (valores.includes(modeloSistema) ? modeloSistema : selectEl.value);
        if (valorPreferido) selectEl.value = valorPreferido;
        return;
      } catch (_) {}
    }
  }

  /* ═══════════════ Inicialização ═══════════════ */
