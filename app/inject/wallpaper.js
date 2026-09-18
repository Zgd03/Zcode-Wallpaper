/**
 * ZCode 壁纸注入载荷（渲染进程主世界执行）。
 *
 * 通过 CDP 注入：控制器把 `window.__zcodeWallpaperConfig` 设为配置 JSON 后，
 * 执行本脚本（立即应用），并在新文档加载时用 `Page.addScriptToEvaluateOnNewDocument`
 * 让本脚本在页面脚本之前运行。
 *
 * 支持两种壁纸：
 *   kind = "image"  → #zcode-wallpaper-layer 用 background-image 显示图片
 *   kind = "video"  → #zcode-wallpaper-video 用 <video autoplay loop muted> 播放视频
 * 暗化蒙层 #zcode-wallpaper-dim 始终在壁纸之上；#root 提升到 z-index:1 保证 UI 在最上。
 *
 * 幂等性：所有样式/属性赋值都先比较；视频的 src 只在变化时设置，避免周期性心跳
 * 反复 load 导致视频闪断/重播。
 *
 * 兜底自愈：background_overrides 的选择器全部落空时（ZCode 升级换了类名），按「面积够大的
 * 不透明 bg-background 表面」自动半透明化，并把命中数/自愈数写到 window.__zcodeWallpaperDiag
 * 供控制器记日志——否则配置过期只会表现为「壁纸被内容区盖住」，没有任何提示。
 */
(function () {
  'use strict';

  var CFG = window.__zcodeWallpaperConfig || {};

  function el(tag, id) {
    var e = document.getElementById(id);
    if (!e) {
      e = document.createElement(tag);
      e.id = id;
      if (tag === 'style') {
        document.head.appendChild(e);
      } else {
        document.body.appendChild(e);
      }
    }
    return e;
  }

  function cssUrl(u) {
    // 防御特殊字符
    return 'url("' + String(u).replace(/"/g, '\\"') + '")';
  }

  // 幂等赋值：内容没变就完全不触碰 element.style，避免周期性心跳导致重绘/闪屏
  function setInline(el, css) {
    if (el.getAttribute('data-wp-css') !== css) {
      el.style.cssText = css;
      el.setAttribute('data-wp-css', css);
    }
  }

  // ---------- 兜底自愈 ----------
  // ZCode 升级会重命名内部类名（例如 3.12.3 把主卡片圆角从 rounded-xl 改成 rounded-[5px]），
  // 配置里的 background_overrides 可能一个都命中不了，于是右侧内容区被不透明卡片整块盖住。
  // 命中数为 0 时按「面积够大 + 不透明的 bg-background 表面」兜底，用同样的半透明值处理，
  // 并打上标记让控制器能写进日志——失效不再是静默的。

  var AUTO_ATTR = 'data-wp-auto';
  // 弹层/菜单不动，避免把浮层文字也弄成半透明
  var AUTO_SKIP = '[role="dialog"],[role="menu"],[role="listbox"],[data-radix-popper-content-wrapper]';

  function isOpaque(el) {
    var bg = getComputedStyle(el).backgroundColor;
    return !(bg === 'transparent' || bg === 'rgba(0, 0, 0, 0)');
  }

  function classTokens(el) {
    var c = el.className;
    if (c && c.baseVal !== undefined) c = c.baseVal;
    return String(c || '').split(/\s+/).filter(Boolean);
  }

  // 自愈用的半透明值：优先沿用 background_overrides 里已有的颜色，避免两套配色不一致
  function pickOverlayColor(overrides) {
    for (var sel in overrides) {
      if (!Object.prototype.hasOwnProperty.call(overrides, sel)) continue;
      var v = String(overrides[sel] || '').trim();
      if (!v) continue;
      var m = v.match(/--color-background\s*:\s*([^;]+)/i);
      if (m) return m[1].trim();
      if (v.indexOf(':') === -1) return v;
    }
    return 'rgba(22, 22, 22, 0.55)';
  }

  function markAutoSurfaces() {
    var viewport = window.innerWidth * window.innerHeight;
    var nodes = document.querySelectorAll('[class*="bg-background"]');
    var i, n, r;
    var surfaces = [];
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      if (n.closest(AUTO_SKIP)) continue;
      r = n.getBoundingClientRect();
      if (r.width * r.height < viewport * 0.3) continue;
      // 已标记的沿用：上一轮被自己改成了半透明，不能再按「本来就透明」判掉
      if (!n.hasAttribute(AUTO_ATTR) && !isOpaque(n)) continue;
      surfaces.push(n);
    }
    // 与主表面共用圆角类的外框细条（卡片边上的 2px 描边条）一起处理，避免留下暗边
    var radii = {};
    for (i = 0; i < surfaces.length; i++) {
      classTokens(surfaces[i]).forEach(function (t) {
        if (t.indexOf('rounded') === 0) radii[t] = 1;
      });
    }
    var keep = surfaces.slice();
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      if (n.closest(AUTO_SKIP) || keep.indexOf(n) !== -1) continue;
      if (!n.hasAttribute(AUTO_ATTR) && !isOpaque(n)) continue;
      var toks = classTokens(n);
      for (var j = 0; j < toks.length; j++) {
        if (radii[toks[j]]) { keep.push(n); break; }
      }
    }
    for (i = 0; i < keep.length; i++) keep[i].setAttribute(AUTO_ATTR, '');
    // 切页面/改尺寸后回收不再需要的标记
    var marked = document.querySelectorAll('[' + AUTO_ATTR + ']');
    for (i = 0; i < marked.length; i++) {
      if (keep.indexOf(marked[i]) === -1) marked[i].removeAttribute(AUTO_ATTR);
    }
    return keep.length;
  }

  function clearAutoSurfaces() {
    var marked = document.querySelectorAll('[' + AUTO_ATTR + ']');
    for (var i = 0; i < marked.length; i++) marked[i].removeAttribute(AUTO_ATTR);
  }

  function applyVideo(video, layer, url, mode, bgColor) {
    setInline(layer, 'display:none;');
    var fit = mode === 'contain' ? 'contain' : (mode === 'fill' ? 'fill' : 'cover');
    setInline(video,
      'position:fixed;inset:0;z-index:0;pointer-events:none;' +
      'width:100%;height:100%;object-fit:' + fit + ';' +
      'background-color:' + bgColor + ';');
    video.setAttribute('autoplay', '');
    video.setAttribute('loop', '');
    video.setAttribute('muted', '');
    video.setAttribute('playsinline', '');
    video.setAttribute('aria-hidden', 'true');
    // 注意：muted 内容属性只影响 defaultMuted；当前静音态必须直接设属性，
    // 否则非静音 autoplay 会被浏览器自动播放策略拦截。
    video.muted = true;
    video.defaultMuted = true;
    // 幂等设置 src：URL 没变就不动，避免心跳重启视频
    if (video.getAttribute('data-wp-src') !== url) {
      video.setAttribute('data-wp-src', url);
      video.src = url;
      var tryPlay = function () {
        var p = video.play();
        if (p && p.catch) p.catch(function () {});
      };
      tryPlay();
      video.addEventListener('canplay', tryPlay, { once: true });
    }
  }

  function applyImage(video, layer, url, mode, position, repeat, bgColor) {
    setInline(video, 'display:none;');
    try { video.pause(); } catch (e) {}
    setInline(layer,
      'position:fixed;inset:0;z-index:0;pointer-events:none;' +
      'background-color:' + bgColor + ';' +
      (url ? ('background-image:' + cssUrl(url) + ';') : '') +
      'background-position:' + position + ';' +
      'background-repeat:' + repeat + ';' +
      'background-size:' + (mode === 'tile' ? 'auto' : mode) + ';');
  }

  function apply() {
    var url = CFG.url || '';
    var kind = CFG.kind || 'image';
    var mode = CFG.mode || 'cover';
    var position = CFG.position || 'center';
    var repeat = CFG.repeat || 'no-repeat';
    var darken = Number(CFG.darken) || 0;
    var bgColor = CFG.bgColor || '#141414';
    var selectors = Array.isArray(CFG.transparentSelectors) ? CFG.transparentSelectors : [];
    // 高级：selector -> background 值（如 rgba(22,22,22,0.55)），优先级高于透明化列表
    var overrides = CFG.backgroundOverrides || {};

    var layer = el('div', 'zcode-wallpaper-layer');
    var video = el('video', 'zcode-wallpaper-video');

    if (kind === 'video') {
      applyVideo(video, layer, url, mode, bgColor);
    } else {
      applyImage(video, layer, url, mode, position, repeat, bgColor);
    }

    // 暗化蒙层（保证文字可读，位于壁纸之上）
    var dim = el('div', 'zcode-wallpaper-dim');
    setInline(dim,
      'position:fixed;inset:0;z-index:0;pointer-events:none;' +
      'background-color:rgba(0,0,0,' + Math.min(0.9, Math.max(0, darken)).toFixed(3) + ');');

    // 样式表：提升 #root 层级 + 透明化目标容器
    // 注意：<style> 的规则在 textContent（不是 style 属性），单独用精确字符串比较
    var style = el('style', 'zcode-wallpaper-style');
    var css = 'html,body,#root{background:transparent !important;}' +
      '#root{position:relative;z-index:1;}' +
      '#zcode-wallpaper-layer,#zcode-wallpaper-dim,#zcode-wallpaper-video{z-index:0;display:block;}' +
      (selectors.map(function (s) { return s + '{background:transparent !important;}'; }).join(''));
    for (var sel in overrides) {
      if (Object.prototype.hasOwnProperty.call(overrides, sel) && sel.trim()) {
        var v = String(overrides[sel]).trim();
        if (v.indexOf(':') === -1) {
          // 纯值 → background
          css += sel + '{background:' + v + ' !important;}';
        } else {
          // 自定义声明（如 --color-background: rgba(...)），多条用 ; 分隔，各加 !important
          var decls = v.split(';').map(function (d) {
            d = d.trim();
            return d ? d + ' !important' : '';
          }).join(';');
          css += sel + '{' + decls + ';}';
        }
      }
    }

    // 命中数为 0 → 配置是按旧版 ZCode 的类名写的，启用兜底自愈（见上方说明）
    var matched = 0;
    var hasOverride = false;
    for (var key in overrides) {
      if (!Object.prototype.hasOwnProperty.call(overrides, key) || !key.trim()) continue;
      hasOverride = true;
      try { matched += document.querySelectorAll(key).length; } catch (e) {}
    }
    var auto = 0;
    if (hasOverride && matched === 0) {
      auto = markAutoSurfaces();
      if (auto) {
        var autoColor = pickOverlayColor(overrides);
        css += '[' + AUTO_ATTR + ']{--color-background:' + autoColor +
          ' !important;background:' + autoColor + ' !important;}';
      }
    } else {
      clearAutoSurfaces();
    }
    if (style.textContent !== css) {
      style.textContent = css;
    }

    // 诊断（控制器读它写日志：命中数 0 说明选择器过期，见 controller.py）
    window.__zcodeWallpaperDiag = JSON.stringify({ matched: matched, auto: auto });
  }

  function start() {
    // #root 可能尚未出现（document-start 注入时），等它就绪再应用
    function ready() {
      if (document.getElementById('root')) {
        apply();
        return true;
      }
      return false;
    }
    if (!ready()) {
      var obs = new MutationObserver(function () {
        if (ready()) obs.disconnect();
      });
      obs.observe(document.documentElement, { childList: true, subtree: true });
    }
  }

  // 暴露给控制器周期调用（幂等，可抵抗重渲染/手动再注入）
  window.__zcodeWallpaperApply = apply;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
