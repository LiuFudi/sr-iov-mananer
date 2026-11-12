/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 LiuFudi
 */
(function () {
  "use strict";

  var BASE = window.__BASE__ || "/";
  if (BASE.charAt(BASE.length - 1) !== "/") BASE += "/";
  var DESKTOP_PAGE_SIZE = 3;
  var MOBILE_PAGE_SIZE = 2;
  var DONATE_QR = window.__SRIOV_DONATE_QR__ || {};
  var state = {
    status: null,
    selected: null,
    donate: null,
    renameDomain: null,
    pages: { vf: 1, vm: 1 },
    busy: false,
    loading: false,
    toastTimer: null,
    lastFocus: null
  };

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }

  function safeExternalUrl(value) {
    try {
      var url = new URL(String(value || ""), window.location.href);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
    } catch (error) {
      return "";
    }
  }

  function api(path, options) {
    return fetch(BASE + path, options).then(function (response) {
      return response.text().then(function (text) {
        var data;
        try {
          data = text ? JSON.parse(text) : {};
        } catch (error) {
          data = { ok: false, error: "响应格式异常（HTTP " + response.status + "）" };
        }
        if (!response.ok && !data.error) {
          data.ok = false;
          data.error = "请求失败（HTTP " + response.status + "）";
        }
        return data;
      });
    });
  }

  function post(path, body) {
    return api(path, {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body || {})
    });
  }

  function showToast(message, kind) {
    var toast = $("toast");
    toast.textContent = message;
    toast.className = "toast " + (kind || "success");
    toast.hidden = false;
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(function () {
      toast.hidden = true;
    }, 3200);
  }

  function showAlert(message, diagnostic) {
    $("alert-text").textContent = message;
    $("alert").hidden = false;
    $("alert-action").hidden = !diagnostic;
  }

  function hideAlert() {
    $("alert").hidden = true;
  }

  function currentStatusLabel(status) {
    if (!status) {
      return { tone: "pending", short: "检测中", title: "正在检查 SR-IOV 状态",
        detail: "请稍候，正在读取驱动、VF 和虚拟机占用信息。", ready: false };
    }
    if (status.sriov_ready) {
      return {
        tone: "ok",
        short: "运行正常",
        title: "运行正常",
        detail: "驱动已激活 SR-IOV，可以调整 VF 数量。",
        ready: true
      };
    }
    if (!status.dkms_module) {
      return {
        tone: "error",
        short: "缺少驱动",
        title: "缺少 DKMS 模块",
        detail: "当前内核未找到 i915-sriov-dkms。安装匹配内核的驱动后可继续。",
        ready: false
      };
    }
    if (status.driver && status.driver.kind === "dkms") {
      return {
        tone: "warn",
        short: "未激活",
        title: "SR-IOV 未激活",
        detail: "DKMS 模块已安装，但 max_vfs 未正确生效。请打开诊断执行修复。",
        ready: false
      };
    }
    return {
      tone: "warn",
      short: "需要修复",
      title: "当前驱动不支持 SR-IOV",
      detail: "当前未使用支持 SR-IOV 的 DKMS 驱动。请打开诊断执行修复。",
      ready: false
    };
  }

  function driverName(status) {
    if (!status || !status.driver) return "未知";
    if (status.driver.kind === "dkms") return status.sriov_ready ? "DKMS 已就绪" : "DKMS 未激活";
    if (status.driver.kind === "stock") return "内核自带版";
    if (status.driver.kind === "none") return "未加载";
    return status.driver.label || "未知";
  }

  function renderSummary(status) {
    var info = currentStatusLabel(status);
    var used = (status && status.vfs_in_use) || [];
    $("health-dot").className = "status-dot " + info.tone;
    $("health-label").textContent = info.short;
    $("health-button").setAttribute("data-tone", info.tone);
    $("health-button").title = info.short;
    $("summary-title").textContent = info.title;
    $("summary-detail").textContent = info.detail;
    $("version-text").textContent = "版本 " + ((status && status.version) || "-");
    $("about-version").textContent = (status && status.version) || "-";
    $("metric-driver").textContent = driverName(status);
    $("metric-driver-detail").textContent = status && status.driver && status.driver.version
      ? "版本 " + status.driver.version
      : (status && status.dkms_module ? "DKMS 模块已安装" : "未找到 DKMS 模块");
    $("metric-capacity").textContent = status && status.sriov_ready
      ? status.totalvfs + " 个"
      : "未激活";
    $("metric-capacity-detail").textContent = status && status.driver && status.driver.kind === "dkms"
      ? "max_vfs = " + status.driver.max_vfs
      : "需要 DKMS 版驱动";
    $("metric-used").textContent = used.length + " 个";
    $("metric-used-detail").textContent = used.length
      ? "涉及 " + used.map(function (item) { return item.vm_label || item.vm; }).join("、")
      : "当前无虚拟机占用";
    $("subtitle").textContent = "更新于 " + new Date().toLocaleTimeString();

    if (info.ready) {
      hideAlert();
    } else {
      showAlert(info.detail, true);
    }
  }

  function targetMaximum() {
    if (!state.status) return 0;
    return Math.max(Number(state.status.totalvfs) || 0, 7);
  }

  function selectedUsable(status) {
    return Math.min(Math.max(Number(state.selected) || 0, 0), targetMaximum());
  }

  function renderManagement(status) {
    var current = Number(status.numvfs) || 0;
    var configured = Number(status.configured_count) || 0;
    var selected = selectedUsable(status);
    var ready = !!status.sriov_ready;
    var used = status.vfs_in_use || [];
    var blockedByUse = used.length > 0;
    var usedNames = used.map(function (item) { return item.vm_label || item.vm; }).join("、");
    var changesCurrent = selected !== current;
    var changesConfigured = selected !== configured;
    var actionLabel;

    state.selected = selected;
    $("selected-count").textContent = String(selected);
    $("configured-badge").textContent = "当前 " + current + " · 开机 " + configured;
    $("count-minus").disabled = state.busy || selected <= 0;
    $("count-plus").disabled = state.busy || selected >= targetMaximum();

    if (blockedByUse) {
      actionLabel = "占用中不可保存";
    } else if (changesCurrent && ready) {
      actionLabel = "保存";
    } else if (changesConfigured) {
      actionLabel = "保存开机数量";
    } else if (changesCurrent && !ready) {
      actionLabel = "需先修复驱动";
    } else {
      actionLabel = "无需更改";
    }
    $("apply-label").textContent = state.busy ? "正在应用" : actionLabel;

    var canApplyCurrent = changesCurrent && ready && !blockedByUse;
    var canSaveConfigured = changesConfigured;
    $("apply-main").disabled = state.busy || blockedByUse || (!canApplyCurrent && !canSaveConfigured);
    $("persist-only").hidden = !(canApplyCurrent && changesConfigured);
    $("persist-only").disabled = state.busy || blockedByUse || !changesConfigured;
    if (blockedByUse) {
      $("apply-main").title = "虚拟机 " + usedNames + " 正在使用 VF，关闭对应虚拟机后才能保存";
    } else {
      $("apply-main").removeAttribute("title");
    }
  }

  function badgeForVf(vf, used) {
    if (used[vf.bdf]) {
      var label = used[vf.bdf].vm_label || used[vf.bdf].vm;
      return '<span class="badge info">' + escapeHtml(label) + " 使用中</span>";
    }
    if (vf.driver === "vfio-pci") {
      return '<span class="badge info">已绑定 vfio-pci</span>';
    }
    return '<span class="badge ok">可用</span>';
  }

  function paginate(items, key) {
    var pageSize = window.matchMedia && window.matchMedia("(max-width: 620px)").matches
      ? MOBILE_PAGE_SIZE : DESKTOP_PAGE_SIZE;
    var totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    state.pages[key] = Math.min(Math.max(state.pages[key] || 1, 1), totalPages);
    var page = state.pages[key];
    return {
      items: items.slice((page - 1) * pageSize, page * pageSize),
      page: page,
      totalPages: totalPages,
      total: items.length,
      pageSize: pageSize
    };
  }

  function renderPager(key, data) {
    var dots = $(key + "-dots");
    dots.innerHTML = "";
    for (var pageNumber = 1; pageNumber <= data.totalPages; pageNumber += 1) {
      (function (targetPage) {
        var dot = document.createElement("button");
        dot.type = "button";
        dot.className = "pager-dot" + (targetPage === data.page ? " active" : "");
        dot.setAttribute("aria-label", "第 " + targetPage + " 页");
        dot.setAttribute("title", "第 " + targetPage + " 页");
        if (targetPage === data.page) dot.setAttribute("aria-current", "page");
        dot.addEventListener("click", function () {
          if (state.pages[key] === targetPage) return;
          state.pages[key] = targetPage;
          renderDevices(state.status);
        });
        dots.appendChild(dot);
      })(pageNumber);
    }
  }

  function renderDevices(status) {
    var used = {};
    (status.vfs_in_use || []).forEach(function (item) {
      used[item.bdf] = item;
    });

    var vfList = $("vf-list");
    var vfs = status.vfs || [];
    var vfPage = paginate(vfs, "vf");
    var vfEmpty = !vfPage.items.length;
    vfList.innerHTML = "";
    vfList.classList.toggle("is-empty", vfEmpty);
    vfList.closest(".list-panel").classList.toggle("is-empty", vfEmpty);
    if (vfEmpty) {
      vfList.innerHTML = '<div class="empty-state"><strong>暂无 VF</strong><span>调整数量或完成诊断后，设备会显示在这里。</span></div>';
    } else {
      vfPage.items.forEach(function (vf) {
        var item = document.createElement("article");
        item.className = "device-card";
        item.innerHTML =
          '<div class="device-top"><code>' + escapeHtml(vf.short) + "</code>" + badgeForVf(vf, used) + "</div>" +
          '<dl class="device-meta"><div><dt>驱动</dt><dd>' + escapeHtml(vf.driver || "-") +
          '</dd></div><div><dt>IOMMU 组</dt><dd>' + escapeHtml(vf.iommu_group || "-") + "</dd></div></dl>";
        vfList.appendChild(item);
      });
    }
    $("vf-count").textContent = (status.numvfs || 0) + " / " + (status.totalvfs || 0);
    renderPager("vf", vfPage);

    var vmList = $("vm-list");
    var vms = status.vms || {};
    var vmLabels = status.vm_labels || {};
    var names = Object.keys(vms);
    var vmPage = paginate(names, "vm");
    var vmEmpty = !vmPage.items.length;
    vmList.innerHTML = "";
    vmList.classList.toggle("is-empty", vmEmpty);
    vmList.closest(".list-panel").classList.toggle("is-empty", vmEmpty);
    if (vmEmpty) {
      vmList.innerHTML = '<div class="empty-state"><strong>无运行中的虚拟机</strong><span>打开虚拟机后，直通设备会自动显示。</span></div>';
    } else {
      vmPage.items.forEach(function (name) {
        var devices = (vms[name] || []).map(function (bdf) {
          return bdf.replace("0000:", "");
        });
        var label = vmLabels[name] || name;
        var item = document.createElement("article");
        item.className = "device-card vm-card";
        item.innerHTML =
          '<div class="vm-tile-head"><div><div class="vm-name" title="' + escapeHtml(label) + '">' +
          escapeHtml(label) + '</div><div class="vm-domain">' +
          escapeHtml(label === name ? "libvirt 域名" : name) + '</div></div>' +
          '<button class="mini-icon vm-edit" type="button" data-domain="' + escapeHtml(name) +
          '" title="自定义名称" aria-label="自定义 ' + escapeHtml(name) + ' 的显示名称">' +
          '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/>' +
          '<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/></svg></button></div>' +
          '<div class="vm-tile-foot"><span class="badge ok">运行中</span><span class="vm-device-count">' +
          (devices.length ? devices.length + " 个直通设备" : "无直通设备") + "</span></div>";
        item.querySelector(".vm-edit").addEventListener("click", function (event) {
          openRename(event.currentTarget.getAttribute("data-domain"));
        });
        vmList.appendChild(item);
      });
    }
    $("vm-count").textContent = names.length + " 台";
    renderPager("vm", vmPage);
  }

  function openRename(domain) {
    if (!domain || !state.status) return;
    var labels = state.status.vm_labels || {};
    var label = labels[domain] || "";
    state.renameDomain = domain;
    $("rename-domain").textContent = "libvirt 域名：" + domain;
    $("vm-alias-input").value = label === domain ? "" : label;
    openModal("rename-modal");
    window.setTimeout(function () {
      $("vm-alias-input").focus();
      $("vm-alias-input").select();
    }, 0);
  }

  function saveVmAlias() {
    if (!state.renameDomain) return;
    var alias = $("vm-alias-input").value.trim();
    $("rename-save").disabled = true;
    post("api/vm-alias", { name: state.renameDomain, alias: alias }).then(function (response) {
      if (!response.ok) {
        showToast(response.error || response.message || "名称保存失败", "error");
        return;
      }
      showToast(response.message || "名称已保存", "success");
      closeModal("rename-modal");
      return reload(true, true);
    }).catch(function (error) {
      showToast(error.message || String(error), "error");
    }).finally(function () {
      $("rename-save").disabled = false;
    });
  }

  function checkItem(status, title, detail) {
    var kind = status === true ? "pass" : (status === "info" ? "info" : "fail");
    var symbol;
    if (kind === "pass") {
      symbol = '<svg class="icon" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>';
    } else if (kind === "info") {
      symbol = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/></svg>';
    } else {
      symbol = '<svg class="icon" viewBox="0 0 24 24"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
    }
    return '<div class="check-item ' + kind + '">' +
      '<span class="check-icon" aria-hidden="true">' +
      symbol +
      '</span><div><strong>' + escapeHtml(title) + "</strong><span>" + escapeHtml(detail) + "</span></div></div>";
  }

  function renderDiagnostics(status, resetControls) {
    if (!status) return;
    var info = currentStatusLabel(status);
    var used = status.vfs_in_use || [];
    var hasModule = !!status.dkms_module;
    var dkms = status.driver && status.driver.kind === "dkms";
    var ready = !!status.sriov_ready;
    var bus = ready ? "max_vfs=" + status.driver.max_vfs + "，totalvfs=" + status.totalvfs
      : (dkms ? "max_vfs=" + status.driver.max_vfs + "，totalvfs=0" : "当前驱动不支持 SR-IOV");

    $("diagnostics-subtitle").textContent = info.detail;
    $("diagnostic-summary").className = "diagnostic-summary " + info.tone;
    $("diagnostic-state").textContent = info.title;
    $("diagnostic-reason").textContent = info.detail;
    $("check-list").innerHTML =
      checkItem(hasModule, "DKMS 模块", status.dkms_module || "当前内核未找到 i915 DKMS 模块") +
      checkItem(dkms, "i915 驱动", status.driver ? status.driver.label : "未检测到驱动") +
      checkItem(ready, "SR-IOV 能力", bus) +
      checkItem(used.length ? "info" : true, "虚拟机占用", used.length
        ? "正常使用中：" + used.map(function (item) { return item.vm_label || item.vm; }).join("、") + "，调整当前数量前需关闭虚拟机"
        : "没有虚拟机占用 VF");
    $("tech-kernel").textContent = status.kernel || "-";
    $("tech-driver").textContent = status.driver ? status.driver.label : "-";
    $("tech-module").textContent = status.dkms_module || "未找到";
    $("tech-vfs").textContent = (status.driver ? status.driver.max_vfs : "-") + " / " + status.totalvfs;
    if (resetControls) $("inject-safe").checked = false;
    $("safety-row").hidden = !hasModule;
    $("inject").hidden = !hasModule;
    $("inject-label").textContent = ready ? "重新检查并同步" : "执行修复注入";
    if (resetControls) {
      $("diagnostic-log").hidden = true;
      $("diagnostic-log").textContent = "";
    }
    updateDiagnosticControls();
  }

  function donateFeedback(parts) {
    var paragraph = document.createElement("p");
    paragraph.className = "donate-feedback";
    if (!Array.isArray(parts)) return paragraph;
    parts.forEach(function (part) {
      if (!part || !part.text) return;
      var text = String(part.text);
      var url = part.url ? safeExternalUrl(part.url) : "";
      var node;
      if (url) {
        node = document.createElement("a");
        node.href = url;
        node.target = "_blank";
        node.rel = "noopener noreferrer";
      } else {
        node = document.createElement("span");
      }
      if (part.accent) node.className = "donate-accent";
      node.textContent = text;
      paragraph.appendChild(node);
    });
    return paragraph;
  }

  function renderDonate() {
    var body = $("donate-body");
    var config = state.donate;
    body.innerHTML = "";
    if (!config) {
      var unavailable = document.createElement("p");
      unavailable.className = "empty-state";
      unavailable.textContent = "打赏配置暂不可用";
      body.appendChild(unavailable);
      return;
    }
    $("donate-title").textContent = config.title || "支持作者";
    if (config.message) {
      var message = document.createElement("p");
      message.className = "donate-message";
      message.textContent = config.message;
      body.appendChild(message);
    }

    var qrcodes = Array.isArray(config.qrcodes) ? config.qrcodes : [];
    if (qrcodes.length) {
      var grid = document.createElement("div");
      grid.className = "donate-qr-grid";
      qrcodes.forEach(function (qr) {
        var figure = document.createElement("figure");
        figure.className = "donate-qr";
        var source = DONATE_QR[qr.key];
        if (source) {
          var image = document.createElement("img");
          image.src = source;
          image.alt = String(qr.label || "收款码");
          figure.appendChild(image);
        } else {
          var missing = document.createElement("div");
          missing.className = "donate-qr-missing";
          missing.textContent = "图片缺失";
          figure.appendChild(missing);
        }
        var label = document.createElement("figcaption");
        label.textContent = String(qr.label || "");
        figure.appendChild(label);
        grid.appendChild(figure);
      });
      body.appendChild(grid);
    }

    var links = Array.isArray(config.links) ? config.links : [];
    if (links.length) {
      var linkRow = document.createElement("div");
      linkRow.className = "donate-links";
      links.forEach(function (item) {
        var url = safeExternalUrl(item && item.url);
        if (!url) return;
        var link = document.createElement("a");
        link.className = "btn secondary";
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = String(item.label || url);
        linkRow.appendChild(link);
      });
      if (linkRow.children.length) body.appendChild(linkRow);
    }
    if (config.note) {
      var note = document.createElement("p");
      note.className = "donate-note";
      note.textContent = config.note;
      body.appendChild(note);
    }
    if (Array.isArray(config.feedback) && config.feedback.length) {
      body.appendChild(donateFeedback(config.feedback));
    }
  }

  function loadDonate() {
    return api("donate.json").then(function (config) {
      state.donate = config && config.ok !== false && typeof config === "object" ? config : null;
    }).catch(function () {
      state.donate = null;
    }).finally(function () {
      var enabled = !state.donate || state.donate.enabled !== false;
      $("donate-button").hidden = !enabled;
      renderDonate();
    });
  }

  function setBusy(busy) {
    state.busy = busy;
    document.body.classList.toggle("is-busy", busy);
    $("refresh").disabled = busy;
    $("about-button").disabled = busy;
    $("health-button").disabled = busy;
    if (state.status) {
      renderManagement(state.status);
      updateDiagnosticControls();
    }
    $("inject").disabled = busy || !$("inject-safe").checked ||
      !state.status || !!((state.status.vfs_in_use || []).length);
  }

  function updateDiagnosticControls() {
    var status = state.status;
    var used = status ? (status.vfs_in_use || []).length : 0;
    var hasModule = !!(status && status.dkms_module);
    $("inject").disabled = state.busy || !hasModule || !$("inject-safe").checked || used > 0;
    if (!hasModule) {
      $("inject-label").textContent = "缺少 DKMS 模块";
    } else if (state.busy) {
      $("inject-label").textContent = "正在执行";
    } else if (status && status.sriov_ready) {
      $("inject-label").textContent = "重新检查并同步";
    } else {
      $("inject-label").textContent = "执行修复注入";
    }
  }

  function openModal(id) {
    state.lastFocus = document.activeElement;
    var modal = $(id);
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    var focusTarget = modal.querySelector("button, input, a");
    if (focusTarget) focusTarget.focus();
  }

  function closeModal(id) {
    var modal = $(id);
    if (!modal) return;
    modal.hidden = true;
    if (!document.querySelector(".modal:not([hidden])")) {
      document.body.classList.remove("modal-open");
    }
    if (state.lastFocus && state.lastFocus.focus) state.lastFocus.focus();
  }

  function closeTopModal() {
    var modals = document.querySelectorAll(".modal:not([hidden])");
    if (modals.length) closeModal(modals[modals.length - 1].id);
  }

  function render(status) {
    state.status = status;
    if (state.selected === null) {
      state.selected = Number(status.configured_count);
      if (!Number.isFinite(state.selected)) state.selected = Number(status.numvfs) || 0;
    }
    renderSummary(status);
    renderManagement(status);
    renderDevices(status);
    renderDiagnostics(status, false);
    document.body.classList.remove("is-loading");
  }

  function reload(keepSelection, silent) {
    if (state.loading) return Promise.resolve();
    var previous = state.selected;
    state.loading = true;
    $("refresh").classList.add("rotating");
    return api("api/status").then(function (response) {
      if (!response || !response.ok || !response.status) {
        throw new Error((response && (response.error || response.message)) || "状态读取失败");
      }
      state.selected = keepSelection ? previous : null;
      render(response.status);
    }).catch(function (error) {
      $("health-dot").className = "status-dot error";
      $("health-label").textContent = "连接异常";
      $("summary-title").textContent = "无法读取设备状态";
      $("summary-detail").textContent = error.message || String(error);
      showAlert("无法读取设备状态：" + (error.message || String(error)), false);
      if (!silent) showToast("状态读取失败", "error");
    }).finally(function () {
      state.loading = false;
      $("refresh").classList.remove("rotating");
      if (state.status) renderManagement(state.status);
    });
  }

  function applyMain() {
    if (!state.status || state.busy) return;
    if ((state.status.vfs_in_use || []).length) {
      showToast("虚拟机正在使用 VF，关闭对应虚拟机后才能保存", "info");
      return;
    }
    var selected = selectedUsable(state.status);
    var current = Number(state.status.numvfs) || 0;
    var used = (state.status.vfs_in_use || []).length;
    var path = selected !== current && state.status.sriov_ready && !used ? "api/set" : "api/persist";
    setBusy(true);
    post(path, { count: selected }).then(function (response) {
      if (!response.ok) {
        if (response.kind === "in_use") {
          showToast(response.message || "VF 正被虚拟机使用", "info");
          return reload(true, true);
        }
        showToast(response.error || response.message || "操作失败", "error");
        return;
      }
      showToast(response.message || "设置已保存", "success");
      state.selected = selected;
      return reload(true, true);
    }).catch(function (error) {
      showToast(error.message || String(error), "error");
    }).finally(function () {
      setBusy(false);
    });
  }

  function persistOnly() {
    if (!state.status || state.busy) return;
    if ((state.status.vfs_in_use || []).length) {
      showToast("虚拟机正在使用 VF，关闭对应虚拟机后才能保存", "info");
      return;
    }
    var selected = selectedUsable(state.status);
    setBusy(true);
    post("api/persist", { count: selected }).then(function (response) {
      if (!response.ok) {
        showToast(response.error || response.message || "保存失败", "error");
        return;
      }
      showToast(response.message || "开机设置已保存", "success");
      return reload(true, true);
    }).catch(function (error) {
      showToast(error.message || String(error), "error");
    }).finally(function () {
      setBusy(false);
    });
  }

  function inject() {
    if (!state.status || state.busy || !$("inject-safe").checked) return;
    var log = $("diagnostic-log");
    log.hidden = false;
    log.textContent = "正在执行驱动检查与 SR-IOV 修复…";
    setBusy(true);
    post("api/inject", { rebuild_initramfs: false }).then(function (response) {
      if (response.log) log.textContent = response.log.join("\n");
      if (response.ok && response.ready) {
        showToast("SR-IOV 已就绪", "success");
      } else if (response.ok) {
        showToast("驱动已返回，但 SR-IOV 仍未就绪", "warning");
      } else if (response.kind === "in_use") {
        showToast(response.message || "VF 正被虚拟机使用", "info");
      } else {
        showToast(response.error || response.message || "注入失败", "error");
      }
      return reload(true, true);
    }).catch(function (error) {
      log.textContent += "\n请求失败：" + (error.message || String(error));
      showToast(error.message || String(error), "error");
    }).finally(function () {
      setBusy(false);
      updateDiagnosticControls();
    });
  }

  function changeTarget(delta) {
    if (!state.status || state.busy) return;
    state.selected = Math.min(Math.max(selectedUsable(state.status) + delta, 0), targetMaximum());
    renderManagement(state.status);
  }

  $("refresh").addEventListener("click", function () {
    reload(true, false);
  });
  $("count-minus").addEventListener("click", function () { changeTarget(-1); });
  $("count-plus").addEventListener("click", function () { changeTarget(1); });
  $("apply-main").addEventListener("click", applyMain);
  $("persist-only").addEventListener("click", persistOnly);
  $("health-button").addEventListener("click", function () {
    if (state.status) renderDiagnostics(state.status, true);
    openModal("diagnostics-modal");
  });
  $("alert-action").addEventListener("click", function () {
    if (state.status) renderDiagnostics(state.status, true);
    openModal("diagnostics-modal");
  });
  $("about-button").addEventListener("click", function () {
    openModal("about-modal");
  });
  $("donate-button").addEventListener("click", function () {
    renderDonate();
    openModal("donate-modal");
  });
  $("inject-safe").addEventListener("change", updateDiagnosticControls);
  $("inject").addEventListener("click", inject);
  $("rename-save").addEventListener("click", saveVmAlias);
  $("vm-alias-input").addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      saveVmAlias();
    }
  });
  document.querySelectorAll("[data-close-modal]").forEach(function (node) {
    node.addEventListener("click", function () {
      closeModal(node.getAttribute("data-close-modal"));
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeTopModal();
  });

  loadDonate();
  reload(false, false);
  window.setInterval(function () {
    if (!state.busy && !state.loading && !document.hidden) {
      reload(true, true);
    }
  }, 10000);
  window.addEventListener("resize", function () {
    if (state.status) renderDevices(state.status);
  });
})();
