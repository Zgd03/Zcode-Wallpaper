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
    if (style.textContent !== css) {
      style.textContent = css;
    }
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
