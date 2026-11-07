"""Automated Browser Macro & CDP Runner for Google Vids AI Avatars.

Because Google Vids (`vids.google.com` / `docs.google.com/videos`) only generates
an AI Avatar for the currently selected scene (Scene 1) on slide import and leaves
Scenes 2..20 as audio-only voiceovers, this tool automates stepping through
Scenes 2..20 in the browser to generate and insert the AI Avatar on every scene.

Supports three execution modes on macOS:
1. **Clipboard + Floating UI Panel (`--copy`, default)**:
   Copies a self-contained JavaScript automation widget to your macOS clipboard
   (`pbcopy`) and writes `output/vids_avatar_macro.js`. Paste it once into your
   open Google Vids tab (`Cmd+Option+J` -> `Cmd+V` -> `Enter`) to launch an
   in-page floating control panel that walks through Scenes 2..20 automatically.
2. **AppleScript Direct Injection (`--inject-chrome`)**:
   Injects the automation widget directly into the active Google Vids tab of your
   front Google Chrome window via macOS Apple Events.
3. **Chrome DevTools Protocol (`--cdp-port 9222`)**:
   Connects over WebSocket to a Chrome instance launched with
   `--remote-debugging-port=9222` (or launches one with `--launch-chrome`) and
   runs the Scene 2..20 AI Avatar loop automatically.
"""

import argparse
import asyncio
import glob
import json
import logging
import os
import subprocess
import urllib.request
from typing import Dict, List, Optional

import vids_exporter

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("vids_avatar_automator")


def load_all_book_scripts(output_dir: str = "output") -> Dict[str, Dict[str, List[str]]]:
    """Loads 20-page scripts across all supported languages (EN, ES, ZH, YUE, PT, JA, KO, FR, IT, DE) for all books in output_dir."""
    catalog: Dict[str, Dict[str, List[str]]] = {}
    all_langs = ("en", "es", "zh", "yue", "pt", "ja", "ko", "fr", "it", "de")
    for story_path in sorted(glob.glob(os.path.join(output_dir, "*", "story.json"))):
        try:
            with open(story_path, "r", encoding="utf-8") as f:
                story = json.load(f)
            folder_name = os.path.basename(os.path.dirname(story_path))
            title = story.get("title", folder_name)
            pages = sorted(story.get("pages", []), key=lambda p: int(p.get("page_number", 1)))
            total = len(pages)
            catalog[title] = {
                lc: [vids_exporter.extract_page_voiceover(story, p, total, lang=lc) for p in pages]
                for lc in all_langs
            }
        except Exception as exc:
            logger.warning(f"Skipping {story_path}: {exc}")
    return catalog


def build_vids_avatar_macro_js(
    start_scene: int = 2,
    end_scene: int = 21,
    cooldown_sec: int = 3,
    avatar_name: str = "Lennon",
    auto_start: bool = False,
    book_scripts: Optional[Dict[str, Dict[str, List[str]]]] = None,
) -> str:
    """Builds the self-contained browser JS macro for automating Google Vids AI Avatars."""
    scripts_json = json.dumps(book_scripts or {}, ensure_ascii=False)
    avatar_name_json = json.dumps(avatar_name)
    auto_start_js = "true" if auto_start else "false"

    return f"""(() => {{
  const EXISTING = document.getElementById('story-agent-vids-automator');
  if (EXISTING) EXISTING.remove();

  // Ensure Google Vids never pauses background-tab operation_info polling (ZVj.prototype.pause)
  try {{
    Object.defineProperty(document, 'hidden', {{ get: () => false, configurable: true }});
    Object.defineProperty(document, 'visibilityState', {{ get: () => 'visible', configurable: true }});
    document.hasFocus = () => true;
    window.addEventListener('visibilitychange', (e) => e.stopImmediatePropagation(), true);
    window.addEventListener('blur', (e) => e.stopImmediatePropagation(), true);
  }} catch (err) {{}}

  const BOOK_SCRIPTS = {scripts_json};
  const DEFAULT_START = {int(start_scene)};
  const DEFAULT_END = {int(end_scene)};
  const DEFAULT_COOLDOWN = {int(cooldown_sec)};
  const DEFAULT_AVATAR = {avatar_name_json};
  const AUTO_START = {auto_start_js};

  let state = {{
    running: false,
    paused: false,
    currentScene: DEFAULT_START,
  }};

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function isVisible(el) {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return (
      rect.width > 4 &&
      rect.height > 4 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none' &&
      parseFloat(style.opacity || '1') > 0.05
    );
  }}

  function simulateClick(el) {{
    if (!el) return false;
    el.scrollIntoView({{ block: 'nearest', inline: 'nearest' }});
    const rect = el.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {{
      el.dispatchEvent(
        new MouseEvent(type, {{
          bubbles: true,
          cancelable: true,
          view: window,
          clientX: cx,
          clientY: cy,
        }})
      );
    }}
    return true;
  }}

    function findSceneElements() {{
      const thumbs = Array.from(document.querySelectorAll('g.punch-filmstrip-thumbnail'))
        .filter((el) => el.getBoundingClientRect().height > 0);
      if (thumbs.length >= 2) {{
        return thumbs.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
      }}
      const explicit = Array.from(
        document.querySelectorAll('[aria-label*="Scene" i], [data-scene-id], [role="option"][aria-label*="scene" i]')
      ).filter(isVisible);
      return explicit.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
    }}

    async function selectScene(sceneNumber) {{
      const panel = findActiveSidebar();
      if (panel) {{
        await ensureCurrentSceneTab(panel);
        const prevBtn = panel.querySelector('[aria-label="Previous script"]');
        const nextBtn = panel.querySelector('[aria-label="Next script"]');
        const match = (panel.innerText || '').match(/Scene\\s+(\\d+)\\s*\\/\\s*(\\d+)/i);
        if (match && prevBtn && nextBtn) {{
          let cur = parseInt(match[1], 10);
          logStatus(`Navigating from Scene ${{cur}} to Scene ${{sceneNumber}}...`);
          let guard = 0;
          while (cur !== sceneNumber && guard < 30) {{
            const btn = cur < sceneNumber ? nextBtn : prevBtn;
            if (btn.getAttribute('aria-disabled') === 'true') break;
            const r = btn.getBoundingClientRect();
            const opts = {{ bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0 }};
            btn.dispatchEvent(new MouseEvent('mousedown', opts));
            btn.dispatchEvent(new MouseEvent('mouseup', opts));
            await sleep(250);
            const m2 = (panel.innerText || '').match(/Scene\\s+(\\d+)\\s*\\/\\s*(\\d+)/i);
            if (!m2 || parseInt(m2[1], 10) === cur) break;
            cur = parseInt(m2[1], 10);
            guard++;
          }}
          if (cur === sceneNumber) {{
            await sleep(400);
            return true;
          }}
        }}
      }}

      const scenes = findSceneElements();
      if (scenes.length >= sceneNumber) {{
        logStatus(`Selecting Scene ${{sceneNumber}} / ${{scenes.length}}...`);
        simulateClick(scenes[sceneNumber - 1]);
        await sleep(650);
        return true;
      }}
      logStatus(`Timeline scene ${{sceneNumber}} not found; sending PageDown...`);
      document.body.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'PageDown', code: 'PageDown', bubbles: true }}));
      await sleep(650);
      return true;
    }}

    function findActiveSidebar() {{
      const labels = ['AI avatar', 'AI voiceover', 'Scripts'];
      for (const label of labels) {{
        const panel = document.querySelector(`div[aria-label="${{label}}"][role="complementary"]`);
        if (panel && panel.offsetParent !== null) return panel;
      }}
      return null;
    }}

    async function ensureSidebarOpen(targetLabel = 'AI avatar') {{
      let panel = document.querySelector(`div[aria-label="${{targetLabel}}"][role="complementary"]`);
      if (panel && panel.offsetParent !== null) return panel;

      const railMap = {{
        'ai avatar': ['Generate an avatar', 'AI avatar'],
        'ai voiceover': ['Generate a voiceover', 'AI voiceover'],
        'scripts': ['Scripts', 'Generate a voiceover'],
      }};
      const candidates = railMap[targetLabel.toLowerCase()] || [targetLabel];
      const railBtns = Array.from(document.querySelectorAll('button[aria-label], [aria-label], [data-tooltip]')).filter(isVisible);
      const trigger = railBtns.find((el) => {{
        const a = (el.getAttribute('aria-label') || el.getAttribute('data-tooltip') || '').trim().toLowerCase();
        return candidates.some((c) => c.toLowerCase() === a);
      }});
      if (trigger) {{
        trigger.click();
        await sleep(800);
      }}
      return document.querySelector(`div[aria-label="${{targetLabel}}"][role="complementary"]`);
    }}

    async function ensureCurrentSceneTab(panel) {{
      if (!panel) return;
      const curTab = panel.querySelector('.docs-sidebar-toggle-tab-left-title');
      if (curTab && isVisible(curTab)) {{
        const r = curTab.getBoundingClientRect();
        const opts = {{ bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0 }};
        curTab.dispatchEvent(new MouseEvent('mousedown', opts));
        curTab.dispatchEvent(new MouseEvent('mouseup', opts));
        await sleep(300);
      }}
    }}

    async function ensureAllScenesTab(panel) {{
      if (!panel) return;
      const allTab = panel.querySelector('.docs-sidebar-toggle-tab-right-title');
      if (allTab && isVisible(allTab)) {{
        const r = allTab.getBoundingClientRect();
        const opts = {{ bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0 }};
        allTab.dispatchEvent(new MouseEvent('mousedown', opts));
        allTab.dispatchEvent(new MouseEvent('mouseup', opts));
        await sleep(400);
      }}
    }}

    function getTargetScriptForScene(sceneNumber, totalScenes, bookTitle, langCode) {{
      const book = BOOK_SCRIPTS[bookTitle];
      if (!book || !book[langCode]) return null;
      const pages = book[langCode];
      // If 21 scenes (Google Vids "Help me create" / AI Slide convert), Scene 2 is a 2.5s visual bumper
      if (totalScenes === 21 && pages.length === 20) {{
        if (sceneNumber === 1) return pages[0];
        if (sceneNumber === 2) return '';
        return pages[sceneNumber - 2] || '';
      }}
      return pages[sceneNumber - 1] !== undefined ? pages[sceneNumber - 1] : null;
    }}

    async function replaceCurrentSceneScript(targetText) {{
      let panel = findActiveSidebar();
      if (!panel) panel = await ensureSidebarOpen('AI voiceover');
      if (!panel) return false;
      await ensureCurrentSceneTab(panel);

      const normTarget = (targetText || '').replace(/\\s+/g, ' ').trim();
      const normPanel = (panel.innerText || '').replace(/\\s+/g, ' ').trim();
      if (normTarget && normPanel.includes(normTarget)) {{
        return true;
      }}
      if (!normTarget && /Add your script/i.test(normPanel)) {{
        return true;
      }}

      const editBtn = panel.querySelector('button.appsFlixScriptsSidebarKeyboardEditButton');
      if (!editBtn) return false;
      editBtn.focus();
      editBtn.click();
      await sleep(180);

      const iframe = document.querySelector('iframe.docs-texteventtarget-iframe');
      if (!iframe || !iframe.contentDocument) return false;
      const win = iframe.contentWindow;
      const doc = iframe.contentDocument;
      const body = doc.body;
      win.focus();
      body.focus();

      // Select all lines from cursor index 0 via Shift+ArrowDown x 35, then Backspace
      for (let i = 0; i < 35; i++) {{
        body.dispatchEvent(new KeyboardEvent('keydown', {{
          key: 'ArrowDown', code: 'ArrowDown', keyCode: 40, which: 40,
          shiftKey: true, bubbles: true, cancelable: true
        }}));
      }}
      body.dispatchEvent(new KeyboardEvent('keydown', {{
        key: 'Backspace', code: 'Backspace', keyCode: 8, which: 8,
        bubbles: true, cancelable: true
      }}));
      await sleep(120);

      if (targetText && targetText.length > 0) {{
        const dt = new DataTransfer();
        dt.setData('text/plain', targetText);
        body.dispatchEvent(new ClipboardEvent('paste', {{
          clipboardData: dt, bubbles: true, cancelable: true
        }}));
        await sleep(220);
      }}
      return true;
    }}

    async function syncAllScriptsAndVoiceovers() {{
      if (state.running) return;
      state.running = true;
      const bookTitle = document.getElementById('sa-book-select').value;
      const langCode = document.getElementById('sa-lang-select').value || 'en';
      if (!bookTitle || !BOOK_SCRIPTS[bookTitle]) {{
        logStatus('⚠️ Please select a book from the Script Catalog dropdown first.');
        state.running = false;
        return;
      }}

      const panel = await ensureSidebarOpen('AI voiceover');
      await ensureCurrentSceneTab(panel);
      const match = (panel ? panel.innerText || '' : '').match(/Scene\\s+\\d+\\s*\\/\\s*(\\d+)/i);
      const totalScenes = match ? parseInt(match[1], 10) : (findSceneElements().length || 20);
      logStatus(`Syncing verbatim storybook text across ${{totalScenes}} scenes for "${{bookTitle}}" (${{langCode.toUpperCase()}})...`);

      for (let s = 1; s <= totalScenes; s++) {{
        if (!state.running) break;
        await selectScene(s);
        const scriptText = getTargetScriptForScene(s, totalScenes, bookTitle, langCode);
        if (scriptText !== null) {{
          await replaceCurrentSceneScript(scriptText);
          logStatus(`✅ Scene ${{s}}/${{totalScenes}}: Script synced (${{scriptText.length}} chars)`);
        }}
      }}

      if (state.running && panel) {{
        logStatus('Switching to "All scenes" to batch-update all voiceovers...');
        await ensureAllScenesTab(panel);
        await sleep(600);
        const updateAllBtn = panel.querySelector('[aria-label="Update all voiceovers"], [aria-label="Insert all voiceovers"]');
        if (updateAllBtn && updateAllBtn.offsetParent !== null) {{
          simulateClick(updateAllBtn);
          await sleep(1200);
          const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
            .filter((d) => d.offsetParent !== null && /Replace all existing voiceovers/i.test(d.innerText || ''));
          if (dialogs.length) {{
            const replaceBtn = Array.from(dialogs[0].querySelectorAll('button, [role="button"]'))
              .find((b) => (b.innerText || '').trim() === 'Replace');
            if (replaceBtn) {{
              simulateClick(replaceBtn);
              logStatus('🔄 Confirmed "Replace all existing voiceovers" — regenerating all audio tracks...');
            }}
          }}
        }} else {{
          logStatus('✅ All voiceovers are already up to date!');
        }}
      }}
      state.running = false;
    }}

    function checkSignedOutDialog() {{
      const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
        .filter((d) => d.offsetParent !== null && /You have been signed out/i.test(d.innerText || ''));
      return dialogs.length > 0;
    }}

    async function ensureAvatarSelected(panel, avatarName) {{
      if (!panel || !avatarName) return true;
      const targetLower = avatarName.trim().toLowerCase();
      const videoEl = panel.querySelector('video');
      const currentSrc = videoEl ? (videoEl.src || '').toLowerCase() : '';
      // Lennon maps to presetavatars/en/preview_actor16_witty.webm
      if (targetLower === 'lennon' && currentSrc.includes('actor16')) {{
        return true;
      }}
      if (panel.dataset.selectedAvatar === targetLower) {{
        return true;
      }}

      const changeBtn = panel.querySelector('[aria-label="Change the avatar"]');
      if (!changeBtn || changeBtn.offsetParent === null) return false;
      logStatus(`Selecting avatar "${{avatarName}}"...`);
      simulateClick(changeBtn);
      await sleep(850);

      const cards = Array.from(
        document.querySelectorAll('.appsFlixPluginsVoiceoversAvatarsSelectionDialogAvatarContainer')
      ).filter((el) => el.offsetParent !== null);
      const matchCard = cards.find((el) => (el.innerText || '').toLowerCase().includes(targetLower));
      if (!matchCard) {{
        logStatus(`⚠️ Avatar "${{avatarName}}" not found in picker dialog; keeping current avatar.`);
        const cancelBtn = document.querySelector('.docs-material-gm-dialog-cancel-button');
        if (cancelBtn) simulateClick(cancelBtn);
        await sleep(300);
        return false;
      }}

      simulateClick(matchCard);
      await sleep(450);
      const selectBtn = document.querySelector('.docs-material-gm-dialog-call-to-action-button');
      if (selectBtn) {{
        simulateClick(selectBtn);
        await sleep(550);
        panel.dataset.selectedAvatar = targetLower;
        logStatus(`✅ Selected avatar "${{avatarName}}".`);
        return true;
      }}
      return false;
    }}

    async function generateAndInsertAvatarForCurrentScene(sceneNumber) {{
      if (checkSignedOutDialog()) {{
        logStatus('🛑 Google Workspace session is signed out ("You have been signed out"). Please sign back in and reload.');
        state.running = false;
        return false;
      }}

      const panel = await ensureSidebarOpen('AI avatar');
      if (!panel) {{
        logStatus(`⚠️ Scene ${{sceneNumber}}: Could not open 'AI avatar' sidebar.`);
        return false;
      }}
      await ensureCurrentSceneTab(panel);

      // Wait if a previous scene's avatar generation is still finishing
      let preWait = 0;
      while (/This might take a few minutes|Generating avatar/i.test(panel.innerText || '') && preWait < 120) {{
        await sleep(1000);
        preWait++;
      }}

      await selectScene(sceneNumber);
      if (DEFAULT_AVATAR) {{
        await ensureAvatarSelected(panel, DEFAULT_AVATAR);
      }}

      const actionBtn = Array.from(panel.querySelectorAll('div[role="button"], button'))
        .find((el) => {{
          const t = (el.innerText || '').trim();
          return (t === 'Insert' || t === 'Update') && el.offsetParent !== null;
        }});
      if (!actionBtn) {{
        logStatus(`⚠️ Scene ${{sceneNumber}}: Could not find 'Insert' or 'Update' button in AI avatar sidebar.`);
        return false;
      }}

      const btnLabel = (actionBtn.innerText || '').trim();
      const ariaLabel = actionBtn.getAttribute('aria-label') || '';
      if (actionBtn.getAttribute('aria-disabled') === 'true' && !/in progress/i.test(ariaLabel)) {{
        logStatus(`Scene ${{sceneNumber}}: '${{btnLabel}}' is disabled (empty script on bumper scene). Skipping...`);
        return true;
      }}

      logStatus(`Scene ${{sceneNumber}}: Clicking '${{btnLabel}}' in AI avatar sidebar...`);
      simulateClick(actionBtn);

      const maxWaitMs = 360000; // Up to 6 minutes per avatar render
      const startMs = Date.now();
      await sleep(2500);

      while (Date.now() - startMs < maxWaitMs) {{
        if (!state.running) return false;

        if (checkSignedOutDialog()) {{
          logStatus('🛑 Google Workspace session expired ("You have been signed out"). Please sign in and reload.');
          state.running = false;
          return false;
        }}

        const hat = document.getElementById('apps-flix-butterbar-hat');
        if (hat && hat.offsetParent !== null && /Something went wrong/i.test(hat.innerText || '')) {{
          logStatus(`🛑 Google Vids server error on Scene ${{sceneNumber}}: "${{(hat.innerText || '').trim()}}".`);
          state.running = false;
          return false;
        }}

        const bodyText = document.body.innerText || '';
        if (/daily limit|quota exceeded|limit reached/i.test(bodyText)) {{
          logStatus(`🛑 Google Vids daily AI Avatar limit reached on Scene ${{sceneNumber}}. Stopping.`);
          state.running = false;
          return false;
        }}

        const curBtn = Array.from(panel.querySelectorAll('div[role="button"], button'))
          .find((el) => {{
            const t = (el.innerText || '').trim();
            return (t === 'Insert' || t === 'Update') && el.offsetParent !== null;
          }});
        const inProgressAria = curBtn && /in progress/i.test(curBtn.getAttribute('aria-label') || '');
        const generatingText = /This might take a few minutes|Generating avatar/i.test(panel.innerText || '');

        if (!generatingText && !inProgressAria && Date.now() - startMs > 4000) {{
          logStatus(`✅ Scene ${{sceneNumber}}: AI Avatar request completed.`);
          return true;
        }}
        await sleep(1500);
      }}

      logStatus(`⚠️ Scene ${{sceneNumber}}: Timed out waiting for avatar render; advancing...`);
      return true;
    }}

    // Build Floating Control Panel UI
    const panel = document.createElement('div');
    panel.id = 'story-agent-vids-automator';
    panel.style.cssText = [
      'position:fixed',
      'top:16px',
      'right:16px',
      'width:360px',
      'z-index:2147483647',
      'background:#1e293b',
      'color:#f8fafc',
      'border:1px solid #475569',
      'border-radius:12px',
      'padding:14px',
      'font-family:system-ui,-apple-system,sans-serif',
      'font-size:13px',
      'box-shadow:0 12px 28px rgba(0,0,0,0.45)',
    ].join(';');

    const bookTitles = Object.keys(BOOK_SCRIPTS);
    const defaultBook = bookTitles.find((t) => t.includes('Lantern-Parade Horse')) || bookTitles[0] || '';
    const bookOptionsHtml = ['<option value="">(Keep current Vids scripts)</option>']
      .concat(bookTitles.map((t) => `<option value="${{t}}" ${{t === defaultBook ? 'selected' : ''}}>${{t}}</option>`))
      .join('');

    const panelHtml = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <strong style="font-size:14px;color:#fbbf24;">✨ Story-Agent Vids Automator</strong>
        <button id="sa-close-btn" style="background:none;border:none;color:#94a3b8;cursor:pointer;font-size:16px;">✕</button>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px;">
        <label style="display:flex;flex-direction:column;gap:3px;font-size:11px;color:#cbd5e1;">
          Start Scene
          <input id="sa-start-scene" type="number" min="1" max="25" value="${{DEFAULT_START}}"
            style="padding:5px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#fff;" />
        </label>
        <label style="display:flex;flex-direction:column;gap:3px;font-size:11px;color:#cbd5e1;">
          End Scene
          <input id="sa-end-scene" type="number" min="1" max="25" value="${{DEFAULT_END}}"
            style="padding:5px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#fff;" />
        </label>
        <label style="display:flex;flex-direction:column;gap:3px;font-size:11px;color:#cbd5e1;">
          Cooldown (s)
          <input id="sa-cooldown" type="number" min="1" max="60" value="${{DEFAULT_COOLDOWN}}"
            style="padding:5px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#fff;" />
        </label>
      </div>
      <div style="display:grid;grid-template-columns:2fr 1fr;gap:8px;margin-bottom:8px;">
        <label style="display:flex;flex-direction:column;gap:3px;font-size:11px;color:#cbd5e1;">
          Verbatim Storybook Catalog
          <select id="sa-book-select" style="padding:5px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#fff;">
            ${{bookOptionsHtml}}
          </select>
        </label>
        <label style="display:flex;flex-direction:column;gap:3px;font-size:11px;color:#cbd5e1;">
          Language
          <select id="sa-lang-select" style="padding:5px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#fff;">
            <option value="en">EN</option>
            <option value="es">ES</option>
            <option value="zh">ZH</option>
            <option value="pt">PT</option>
            <option value="ja">JA</option>
            <option value="ko">KO</option>
            <option value="fr">FR</option>
            <option value="it">IT</option>
            <option value="de">DE</option>
          </select>
        </label>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:8px;">
        <button id="sa-sync-scripts-btn" style="flex:1;padding:8px;border:none;border-radius:6px;background:#38bdf8;color:#082f49;font-weight:700;cursor:pointer;">
          📝 Sync All Scripts + Voiceovers
        </button>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:10px;">
        <button id="sa-run-btn" style="flex:1;padding:8px;border:none;border-radius:6px;background:#10b981;color:#052e16;font-weight:700;cursor:pointer;">
          ▶ Auto-Generate Avatars
        </button>
        <button id="sa-stop-btn" style="padding:8px 12px;border:none;border-radius:6px;background:#ef4444;color:#fff;font-weight:600;cursor:pointer;">
          ■ Stop
        </button>
      </div>
      <div id="sa-status-log" style="background:#0f172a;border:1px solid #334155;border-radius:6px;padding:8px;height:105px;overflow-y:auto;font-family:monospace;font-size:11px;line-height:1.4;color:#93c5fd;">
        Ready. Click <b>📝 Sync All Scripts + Voiceovers</b> to enforce verbatim storybook text, or <b>▶ Auto-Generate Avatars</b>.
      </div>
    `;
    if (window.trustedTypes && window.trustedTypes.createPolicy) {{
      if (!window.__saTrustedPolicy) {{
        window.__saTrustedPolicy = window.trustedTypes.createPolicy('story-agent-vids-ui', {{
          createHTML: (s) => s,
        }});
      }}
      panel.innerHTML = window.__saTrustedPolicy.createHTML(panelHtml);
    }} else {{
      panel.innerHTML = panelHtml;
    }}

    document.body.appendChild(panel);

    const statusBox = document.getElementById('sa-status-log');
    function logStatus(msg) {{
      const time = new Date().toLocaleTimeString();
      const line = document.createElement('div');
      line.textContent = `[${{time}}] ${{msg}}`;
      statusBox.appendChild(line);
      statusBox.scrollTop = statusBox.scrollHeight;
      console.log(`[Story-Agent Vids Automator] ${{msg}}`);
    }}

    async function runLoop() {{
      if (state.running) return;
      state.running = true;
      const startScene = parseInt(document.getElementById('sa-start-scene').value || '2', 10);
      const endScene = parseInt(document.getElementById('sa-end-scene').value || '21', 10);
      const cooldownSec = parseInt(document.getElementById('sa-cooldown').value || '3', 10);

      logStatus(`Starting AI Avatar loop for Scenes ${{startScene}}..${{endScene}}...`);
      for (let s = startScene; s <= endScene; s++) {{
        if (!state.running) {{
          logStatus('Stopped by user.');
          break;
        }}
        state.currentScene = s;
        try {{
          const ok = await generateAndInsertAvatarForCurrentScene(s);
          if (!ok || !state.running) break;
        }} catch (err) {{
          logStatus(`⚠️ Error on Scene ${{s}}: ${{err && err.message ? err.message : err}}`);
        }}

        if (s < endScene) {{
          logStatus(`Waiting ${{cooldownSec}}s cooldown before Scene ${{s + 1}}...`);
          await sleep(cooldownSec * 1000);
        }}
      }}
      state.running = false;
      logStatus('🎉 Finished AI Avatar automation batch!');
    }}

    document.getElementById('sa-sync-scripts-btn').addEventListener('click', syncAllScriptsAndVoiceovers);
    document.getElementById('sa-run-btn').addEventListener('click', runLoop);
    document.getElementById('sa-stop-btn').addEventListener('click', () => {{
      state.running = false;
      logStatus('Stopping after current step...');
    }});
    document.getElementById('sa-close-btn').addEventListener('click', () => {{
      state.running = false;
      panel.remove();
    }});

    if (AUTO_START) {{
      setTimeout(runLoop, 1500);
    }}
  }})();"""


def copy_to_macos_clipboard(text: str) -> bool:
    """Copies text to the macOS clipboard via pbcopy."""
    try:
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc.communicate(input=text.encode("utf-8"))
        return proc.returncode == 0
    except Exception as exc:
        logger.warning(f"pbcopy failed: {exc}")
        return False


def inject_into_chrome_via_applescript(js_code: str) -> bool:
    """Injects JS directly into the open Google Vids tab in Google Chrome on macOS via AppleScript."""
    main_world_bg_fix = (
        "javascript:(function(){try{"
        "Object.defineProperty(document,'hidden',{get:()=>false,configurable:true});"
        "Object.defineProperty(document,'visibilityState',{get:()=>'visible',configurable:true});"
        "document.hasFocus=()=>true;"
        "window.addEventListener('visibilitychange',e=>e.stopImmediatePropagation(),true);"
        "window.addEventListener('blur',e=>e.stopImmediatePropagation(),true);"
        "}catch(e){}})();void(0);"
    )
    apple_script = """
    on run argv
        set jsCode to item 1 of argv
        set bgFixUrl to item 2 of argv
        tell application "Google Chrome"
            if (count of windows) = 0 then error "No Chrome windows open"
            set injected to false
            repeat with w in windows
                repeat with t in tabs of w
                    if (URL of t contains "docs.google.com/videos") or (URL of t contains "vids.google.com") then
                        set URL of t to bgFixUrl
                        tell t to execute javascript jsCode
                        set injected to true
                        exit repeat
                    end if
                end repeat
                if injected then exit repeat
            end repeat
            if not injected then
                set URL of active tab of front window to bgFixUrl
                execute active tab of front window javascript jsCode
            end if
        end tell
    end run
    """
    try:
        res = subprocess.run(
            ["osascript", "-e", apple_script, js_code, main_world_bg_fix],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            logger.info("Successfully injected Story-Agent Vids Automator into Google Vids Chrome tab!")
            return True
        logger.warning(f"AppleScript injection note: {res.stderr.strip()}")
        return False
    except Exception as exc:
        logger.warning(f"AppleScript execution failed: {exc}")
        return False


async def inject_via_cdp(
    cdp_port: int,
    js_code: str,
    vids_url: Optional[str] = None,
) -> bool:
    """Connects to Chrome DevTools Protocol over WebSocket and injects the automation macro."""
    import websockets

    targets_url = f"http://127.0.0.1:{cdp_port}/json"
    try:
        with urllib.request.urlopen(targets_url, timeout=5) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error(f"Could not connect to Chrome CDP at {targets_url}: {exc}")
        return False

    page_target = None
    for t in targets:
        if t.get("type") == "page" and (
            "docs.google.com/videos" in t.get("url", "")
            or "vids.google.com" in t.get("url", "")
        ):
            page_target = t
            break

    if not page_target:
        for t in targets:
            if t.get("type") == "page":
                page_target = t
                break

    if not page_target or "webSocketDebuggerUrl" not in page_target:
        logger.error("No inspectable page tab found in Chrome CDP.")
        return False

    ws_url = page_target["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws:
        msg_id = 1
        if vids_url and vids_url not in page_target.get("url", ""):
            await ws.send(
                json.dumps({"id": msg_id, "method": "Page.navigate", "params": {"url": vids_url}})
            )
            await ws.recv()
            msg_id += 1
            await asyncio.sleep(5)

        await ws.send(
            json.dumps(
                {
                    "id": msg_id,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js_code, "awaitPromise": False},
                }
            )
        )
        reply = await ws.recv()
        logger.info(f"CDP Runtime.evaluate response: {reply[:200]}")
        return True


def launch_chrome_with_cdp(cdp_port: int = 9222, url: str = "https://vids.google.com") -> None:
    """Launches macOS Google Chrome with a persistent profile and remote debugging enabled."""
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    profile_dir = os.path.expanduser("~/.story-agent-chrome-vids")
    os.makedirs(profile_dir, exist_ok=True)
    cmd = [
        chrome_bin,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={profile_dir}",
        url,
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.info(f"Launched Google Chrome on CDP port {cdp_port} (profile: {profile_dir})")


def run_applescript_avatar_loop(
    start_scene: int = 2,
    end_scene: int = 21,
    vids_url: Optional[str] = None,
    only_missing: bool = False,
) -> None:
    """Drives Google Vids AI Avatar generation from Python via AppleScript without Chrome background tab throttling."""
    import time

    url_match_expr = (
        f'(URL of t contains "{vids_url}")'
        if vids_url
        else '(URL of t contains "docs.google.com/videos") or (URL of t contains "vids.google.com")'
    )
    only_missing_js = "true" if only_missing else "false"

    def run_vids_js(js_body: str, tick_ms: int = 0) -> str:
        tick_cmd = (
            f'set URL of t to "javascript:if(window.__saTick)window.__saTick({tick_ms});void(0);"\n'
            if tick_ms > 0
            else ""
        )
        apple = f"""
        on run argv
          set jsCode to item 1 of argv
          tell application "Google Chrome"
            repeat with w in windows
              repeat with t in tabs of w
                if {url_match_expr} then
                  {tick_cmd}
                  return execute t javascript jsCode
                end if
              end repeat
            end repeat
          end tell
        end run
        """
        res = subprocess.run(["osascript", "-e", apple, js_body], capture_output=True, text=True, check=False)
        return res.stdout.strip()

    # Stop any in-page throttled loop
    run_vids_js(
        """
        (function() {
          const stopBtn = document.getElementById("sa-stop-btn");
          if (stopBtn) stopBtn.click();
          return "stopped";
        })()
        """
    )

    for target_scene in range(start_scene, end_scene + 1):
        t0 = time.time()
        # Wait if currently generating
        for _ in range(50):
            raw = run_vids_js(
                """
                (function() {
                  const panel = document.querySelector('div[aria-label="AI avatar"][role="complementary"]');
                  const btn = panel ? Array.from(panel.querySelectorAll('div[role="button"], button')).find(el => {
                    const txt = (el.innerText || "").trim();
                    return (txt === "Insert" || txt === "Update") && el.offsetParent !== null;
                  }) : null;
                  const inProg = btn ? /in progress/i.test(btn.getAttribute("aria-label") || "") : false;
                  const genTxt = panel ? /Generating avatar|This might take a few minutes/i.test(panel.innerText || "") : false;
                  return JSON.stringify({
                    generating: genTxt || inProg
                  });
                })()
                """,
                tick_ms=12000,
            )
            if not raw:
                time.sleep(2.0)
                continue
            try:
                st = json.loads(raw)
            except Exception:
                time.sleep(2.0)
                continue
            if not st.get("generating"):
                break
            time.sleep(2.5)

        nav_js = f"""
        (function() {{
          const panel = document.querySelector('div[aria-label="AI avatar"][role="complementary"]');
          if (!panel) return JSON.stringify({{err: "no_panel"}});
          const prevBtn = panel.querySelector('[aria-label="Previous script"]');
          const nextBtn = panel.querySelector('[aria-label="Next script"]');
          for (let step = 0; step < 25; step++) {{
            const m = (panel.innerText || "").match(/Scene\\s+(\\d+)\\s*\\/\\s*(\\d+)/i);
            if (!m) break;
            const cur = parseInt(m[1], 10);
            if (cur === {target_scene}) break;
            const btn = cur < {target_scene} ? nextBtn : prevBtn;
            if (!btn || btn.getAttribute("aria-disabled") === "true") break;
            const r = btn.getBoundingClientRect();
            const opts = {{ bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0 }};
            btn.dispatchEvent(new MouseEvent("mousedown", opts));
            btn.dispatchEvent(new MouseEvent("mouseup", opts));
            btn.dispatchEvent(new MouseEvent("click", opts));
          }}
          const mFinal = (panel.innerText || "").match(/Scene\\s+(\\d+)\\s*\\/\\s*(\\d+)/i);
          const curScene = mFinal ? parseInt(mFinal[1], 10) : -1;
          const actionBtn = Array.from(panel.querySelectorAll('div[role="button"], button')).find(el => {{
            const txt = (el.innerText || "").trim();
            return (txt === "Insert" || txt === "Update") && el.offsetParent !== null;
          }});
          const btnLabel = actionBtn ? (actionBtn.innerText || "").trim() : null;
          let clicked = null;
          if ({only_missing_js} && btnLabel === "Update") {{
            clicked = "SKIP_ALREADY_UPDATED";
          }} else if (actionBtn && actionBtn.getAttribute("aria-disabled") !== "true") {{
            clicked = btnLabel;
            const r = actionBtn.getBoundingClientRect();
            const opts = {{ bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0 }};
            actionBtn.dispatchEvent(new MouseEvent("mousedown", opts));
            actionBtn.dispatchEvent(new MouseEvent("mouseup", opts));
            actionBtn.dispatchEvent(new MouseEvent("click", opts));
          }}
          return JSON.stringify({{
            curScene,
            btnLabel,
            clicked,
            videos: document.querySelectorAll('g[id*="zindexedVideo"]').length
          }});
        }})()
        """
        start_raw = ""
        for _ in range(5):
            start_raw = run_vids_js(nav_js)
            if start_raw:
                break
            time.sleep(1.0)
        start_info = json.loads(start_raw) if start_raw else {}
        clicked_label = start_info.get("clicked")
        cur_sc = start_info.get("curScene")
        vids_cnt = start_info.get("videos")
        if clicked_label == "SKIP_ALREADY_UPDATED":
            logger.info(f"✅ [Scene {target_scene}] Already has avatar ('Update'); skipping. (videos={vids_cnt})")
            continue
        logger.info(f"[Scene {target_scene}] Triggered: clicked={clicked_label} curScene={cur_sc} videos={vids_cnt}")
        if not clicked_label:
            continue

        time.sleep(3.0)
        idle_streak = 0
        for _ in range(60):
            poll_raw = run_vids_js(
                """
                (function() {
                  const panel = document.querySelector('div[aria-label="AI avatar"][role="complementary"]');
                  const txt = panel ? (panel.innerText || "") : "";
                  const btn = panel ? Array.from(panel.querySelectorAll('div[role="button"], button')).find(el => {
                    const t = (el.innerText || "").trim();
                    return (t === "Insert" || t === "Update") && el.offsetParent !== null;
                  }) : null;
                  const inProg = btn ? /in progress/i.test(btn.getAttribute("aria-label") || "") : false;
                  const generating = /Generating avatar|This might take a few minutes/i.test(txt) || inProg;
                  return JSON.stringify({
                    generating,
                    btnLabel: btn ? (btn.innerText || "").trim() : null,
                    videos: document.querySelectorAll('g[id*="zindexedVideo"]').length
                  });
                })()
                """,
                tick_ms=12000,
            )
            if not poll_raw:
                time.sleep(2.0)
                continue
            try:
                pinfo = json.loads(poll_raw)
            except Exception:
                time.sleep(2.0)
                continue
            if not pinfo.get("generating"):
                idle_streak += 1
                if idle_streak >= 2:
                    dt = time.time() - t0
                    final_vids = pinfo.get("videos")
                    logger.info(
                        f"✅ [Scene {target_scene}] Completed in {dt:.1f}s! (btn={pinfo.get('btnLabel')}, videos={final_vids})"
                    )
                    break
                time.sleep(1.5)
                continue
            idle_streak = 0
            time.sleep(3.0)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Automate Google Vids AI Avatar generation across Scenes 2..21."
    )
    parser.add_argument("--start-scene", type=int, default=2, help="First scene to generate avatar for (default: 2).")
    parser.add_argument("--end-scene", type=int, default=21, help="Last scene to generate avatar for (default: 21).")
    parser.add_argument(
        "--cooldown",
        type=int,
        default=3,
        help="Seconds to wait between scenes (default: 3).",
    )
    parser.add_argument(
        "--avatar",
        default="Lennon",
        help="Avatar name to select (default: 'Lennon' Classic 2D Cartoon avatar).",
    )
    parser.add_argument(
        "--output-js",
        default="output/vids_avatar_macro.js",
        help="Path to write the standalone browser console macro JS file.",
    )
    parser.add_argument(
        "--inject-chrome",
        action="store_true",
        help="Inject directly into the active Google Chrome tab via macOS AppleScript.",
    )
    parser.add_argument(
        "--run-applescript-loop",
        action="store_true",
        help="Drive the Scene loop directly from Python via AppleScript with background timer unthrottling.",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Skip scenes whose button already says 'Update' and only generate scenes with 'Insert'.",
    )
    parser.add_argument(
        "--launch-chrome",
        action="store_true",
        help="Launch Chrome with --remote-debugging-port=9222 and inject over CDP.",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=0,
        help="Connect to an existing Chrome CDP port (e.g. 9222) and inject the automation widget.",
    )
    parser.add_argument(
        "--vids-url",
        default="",
        help="Optional Google Vids document URL or ID to target.",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="Immediately start the Scene 2..20 loop upon injection.",
    )
    args = parser.parse_args(argv)

    catalog = load_all_book_scripts("output")
    js_code = build_vids_avatar_macro_js(
        start_scene=args.start_scene,
        end_scene=args.end_scene,
        cooldown_sec=args.cooldown,
        avatar_name=args.avatar,
        auto_start=args.auto_start,
        book_scripts=catalog,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output_js)), exist_ok=True)
    with open(args.output_js, "w", encoding="utf-8") as f:
        f.write(js_code)
    logger.info(f"Wrote Google Vids Avatar Macro ({len(js_code):,} bytes) to {args.output_js}")

    if copy_to_macos_clipboard(js_code):
        logger.info("Copied macro to macOS clipboard! Press Cmd+Option+J in Google Vids, then Cmd+V and Enter.")

    if args.inject_chrome:
        inject_into_chrome_via_applescript(js_code)

    if args.run_applescript_loop:
        run_applescript_avatar_loop(
            start_scene=args.start_scene,
            end_scene=args.end_scene,
            vids_url=args.vids_url or None,
            only_missing=args.only_missing,
        )

    if args.launch_chrome:
        port = args.cdp_port or 9222
        launch_chrome_with_cdp(cdp_port=port, url=args.vids_url or "https://vids.google.com")

    if args.cdp_port:
        asyncio.run(inject_via_cdp(args.cdp_port, js_code, vids_url=args.vids_url or None))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
