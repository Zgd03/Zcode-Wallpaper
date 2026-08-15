/**
 * ZCode 壁纸注入载荷（渲染进程主世界执行）。
 *
 * 通过 CDP 注入：控制器把 `window.__zcodeWallpaperConfig` 设为配置 JSON 后，
 * 执行本脚本（立即应用），并在新文档加载时用 `Page.addScriptToEvaluateOnNewDocument`
 * 让本脚本在页面脚本之前运行。
 *
 * 原理：
 *   ZCode 的 html/body/#root 本身已是透明（background:0 0 !important）。
 *   这里在 body 下插入两个固定层（壁纸 + 暗化蒙层，z-index:0，pointer-events:none），
 *   并把 #root 提升为 position:relative; z-index:1，使整个 App UI 位于壁纸之上；
 *   再通过配置的透明化选择器把应用里“整块背景容器”置为透明，露出壁纸。
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

  function apply() {
    var url = CFG.url || '';
    var mode = CFG.mode || 'cover';
    var position = CFG.position || 'center';
    var repeat = CFG.repeat || 'no-repeat';
    var darken = Number(CFG.darken) || 0;
    var bgColor = CFG.bgColor || '#141414';
    var selectors = Array.isArray(CFG.transparentSelectors) ? CFG.transparentSelectors : [];
    // 高级：selector -> background 值（如 rgba(22,22,22,0.55)），优先级高于透明化列表
    var overrides = CFG.backgroundOverrides || {};

    // 壁纸层
    var layer = el('div', 'zcode-wallpaper-layer');
    setInline(layer,
      'position:fixed;inset:0;z-index:0;pointer-events:none;' +
      'background-color:' + bgColor + ';' +
      (url ? ('background-image:' + cssUrl(url) + ';') : '') +
      'background-position:' + position + ';' +
      'background-repeat:' + repeat + ';' +
      'background-size:' + (mode === 'tile' ? 'auto' : mode) + ';');

    // 暗化蒙层（保证文字可读）
    var dim = el('div', 'zcode-wallpaper-dim');
    setInline(dim,
      'position:fixed;inset:0;z-index:0;pointer-events:none;' +
      'background-color:rgba(0,0,0,' + Math.min(0.9, Math.max(0, darken)).toFixed(3) + ');');

    // 样式表：提升 #root 层级 + 透明化目标容器
    // 注意：<style> 的规则在 textContent（不是 style 属性），单独用精确字符串比较
    var style = el('style', 'zcode-wallpaper-style');
    var css = 'html,body,#root{background:transparent !important;}' +
      '#root{position:relative;z-index:1;}' +
      '#zcode-wallpaper-layer,#zcode-wallpaper-dim{z-index:0;display:block;}' +
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
