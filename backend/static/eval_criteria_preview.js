/**
 * Kriterien-Vorschau: Tabellen-Editor, Validierung, JSON-Sync (Offertbeurteilung).
 */
(function () {
  const root = document.getElementById('criteria-preview-app');
  if (!root) return;

  const manageMode = root.dataset.manageMode === '1';
  const rankingLabels = JSON.parse(root.dataset.rankingLabels || '{}');
  const initialEl = document.getElementById('criteria-payload-initial');
  let state = { eignung: [], zuschlag: [] };
  let deletedIds = [];

  function esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/"/g, '&quot;');
  }

  function normEignung(e) {
    return {
      id: e && e.id != null ? e.id : null,
      name: (e && e.name) || '',
      description: (e && e.description) || '',
      requirement_ref: (e && e.requirement_ref) || '',
      scale_max: 1,
      children: ((e && e.children) || []).map(function (ch) {
        return {
          id: ch.id != null ? ch.id : null,
          name: ch.name || '',
          description: ch.description || '',
          requirement_ref: (ch.requirement_ref) || inferChildRef(ch.name) || '',
          scale_max: 1,
        };
      }),
    };
  }

  function normZuschlag(e) {
    return {
      id: e && e.id != null ? e.id : null,
      name: (e && e.name) || '',
      description: (e && e.description) || '',
      weight_pct: Number(e && e.weight_pct != null ? e.weight_pct : 0),
      scale_max: Number(e && e.scale_max != null ? e.scale_max : 10),
      ranking_phase: Number(e && e.ranking_phase != null ? e.ranking_phase : 1),
      auto_price: !!(e && e.auto_price),
      requirement_ref: (e && e.requirement_ref) || '',
      children: ((e && e.children) || []).map(function (ch) {
        return {
          id: ch.id != null ? ch.id : null,
          name: ch.name || '',
          description: ch.description || '',
          requirement_ref: (ch.requirement_ref) || inferChildRef(ch.name) || '',
          scale_max: Number(ch.scale_max != null ? ch.scale_max : 10),
        };
      }),
    };
  }

  function loadInitial() {
    try {
      const raw = initialEl ? JSON.parse(initialEl.textContent || '{}') : {};
      state.eignung = (raw.eignung || []).map(normEignung);
      state.zuschlag = (raw.zuschlag || []).map(normZuschlag);
    } catch (e) {
      state = { eignung: [], zuschlag: [] };
    }
  }

  function weightTotal() {
    return state.zuschlag.reduce(function (sum, row) {
      return sum + (parseFloat(row.weight_pct) || 0);
    }, 0);
  }

  function parseExpectedChildCount(text, ref) {
    const t = String(text || '').trim();
    if (!t) return null;
    let     m = t.match(/(\d+)\s+Einzelanforderungen/i);
    if (m) return parseInt(m[1], 10);
    m = t.match(/([A-Za-z])-?0*(\d+)-0*(\d+)\s*(?:bis|–|-|to)\s*\1-?0*(\d+)-0*(\d+)/i);
    if (m) return Math.max(1, parseInt(m[5], 10) - parseInt(m[3], 10) + 1);
    m = t.match(/([A-Za-z])-?0*(\d+)\s*(?:bis|–|-|to)\s*\1-?0*(\d+)/i);
    if (m) return Math.max(1, parseInt(m[3], 10) - parseInt(m[2], 10) + 1);
    return null;
  }

  function childCompleteness() {
    const out = [];
    state.zuschlag.forEach(function (row) {
      if (row.auto_price) return;
      const name = (row.name || '').trim();
      if (!name) return;
      const ref = (row.requirement_ref || '').trim();
      let expected = parseExpectedChildCount(row.description, ref);
      if (expected == null) expected = parseExpectedChildCount(name, ref);
      const found = (row.children || []).length;
      if (expected != null) {
        out.push({
          name: name,
          ref: ref,
          found: found,
          expected: expected,
          complete: found >= expected,
        });
      }
    });
    return out;
  }

  function meta() {
    const wt = weightTotal();
    const weightOk = state.zuschlag.length === 0 || Math.abs(wt - 100) <= 1;
    const emptyDesc = [];
    function scan(list) {
      list.forEach(function (row) {
        if ((row.name || '').trim() && !(row.description || '').trim()) {
          emptyDesc.push(row.name.trim());
        }
        (row.children || []).forEach(function (ch) {
          if ((ch.name || '').trim() && !(ch.description || '').trim()) {
            emptyDesc.push(ch.name.trim());
          }
        });
      });
    }
    scan(state.eignung);
    scan(state.zuschlag);
    return {
      eignung_count: state.eignung.length,
      zuschlag_count: state.zuschlag.length,
      weight_total: Math.round(wt * 10) / 10,
      weight_ok: weightOk,
      missing_eignung: state.eignung.length === 0,
      empty_descriptions: emptyDesc,
      requires_confirm: state.eignung.length === 0 || !weightOk,
      completeness: childCompleteness(),
    };
  }

  function toPayload() {
    function rowId(id) {
      return id != null ? { id: id } : {};
    }
    return {
      eignung: state.eignung.map(function (e) {
        return Object.assign(rowId(e.id), {
          name: e.name.trim(),
          description: e.description.trim(),
          requirement_ref: (e.requirement_ref || '').trim(),
          scale_max: 1,
          children: (e.children || []).filter(function (ch) { return (ch.name || '').trim(); }).map(function (ch) {
            return Object.assign(rowId(ch.id), {
              name: ch.name.trim(),
              description: ch.description.trim(),
              requirement_ref: (ch.requirement_ref || '').trim(),
              scale_max: 1,
            });
          }),
        });
      }).filter(function (e) { return e.name; }),
      zuschlag: state.zuschlag.map(function (z) {
        return Object.assign(rowId(z.id), {
          name: z.name.trim(),
          description: z.description.trim(),
          requirement_ref: (z.requirement_ref || '').trim(),
          weight_pct: parseFloat(z.weight_pct) || 0,
          scale_max: parseInt(z.scale_max, 10) || 10,
          ranking_phase: parseInt(z.ranking_phase, 10) || 1,
          auto_price: !!z.auto_price,
          children: (z.children || []).filter(function (ch) { return (ch.name || '').trim(); }).map(function (ch) {
            return Object.assign(rowId(ch.id), {
              name: ch.name.trim(),
              description: ch.description.trim(),
              requirement_ref: (ch.requirement_ref || '').trim(),
              scale_max: parseInt(ch.scale_max, 10) || 10,
            });
          }),
        });
      }).filter(function (z) { return z.name; }),
    };
  }

  function syncHidden() {
    const payload = toPayload();
    const json = JSON.stringify(payload, null, 2);
    const hidden = document.getElementById('criteria-json-hidden');
    if (hidden) hidden.value = json;
    const pre = document.getElementById('criteria-json-preview');
    if (pre) pre.textContent = json;
    const del = document.getElementById('criteria-deleted-ids');
    if (del) del.value = JSON.stringify(deletedIds);
    return payload;
  }

  function trackDelete(row) {
    if (!manageMode || !row || row.id == null) return;
    const id = parseInt(row.id, 10);
    if (!isNaN(id) && deletedIds.indexOf(id) < 0) deletedIds.push(id);
  }

  function descClass(text) {
    return (text || '').trim() ? '' : ' criteria-desc-empty';
  }

  function phaseOptions(selected) {
    return Object.keys(rankingLabels).map(function (k) {
      const v = parseInt(k, 10);
      const sel = v === selected ? ' selected' : '';
      return '<option value="' + v + '"' + sel + '>' + esc(rankingLabels[k]) + '</option>';
    }).join('');
  }

  function displayRef(ref) {
    const s = String(ref || '').trim();
    if (!s) return '';
    if (/^EK\d+$/i.test(s)) return s.toUpperCase();
    const line = s.match(/^([A-Za-z])-?0*(\d+)-0*(\d+)$/i);
    if (line) {
      return line[1].toUpperCase() + '-' + String(line[2]).padStart(2, '0')
        + '-' + String(line[3]).padStart(3, '0');
    }
    const blk = s.match(/^([A-Za-z])0*(\d+)$/i);
    if (blk) return blk[1].toUpperCase() + '-' + String(blk[2]).padStart(2, '0');
    return s;
  }

  function inferChildRef(name) {
    const m = String(name || '').trim().match(/^([A-Za-z])-?0*(\d+)-0*(\d+)$/i);
    if (!m) return '';
    return m[1].toUpperCase() + '-' + String(m[2]).padStart(2, '0')
      + '-' + String(m[3]).padStart(3, '0');
  }

  function refInput(value, cls, isChild) {
    const ph = isChild ? 'F01-001' : 'EK1 / F-01';
    return '<input class="form-input ' + cls + '" value="' + esc(displayRef(value)) + '" '
      + 'placeholder="' + ph + '" title="Referenzschlüssel aus Pflichtenheft" style="width:4.5rem;font-size:.75rem" />';
  }

  function renderEignung() {
    const tbody = document.getElementById('criteria-eignung-body');
    if (!tbody) return;
    let html = '';
    state.eignung.forEach(function (row, idx) {
      html += '<tr class="criteria-row" data-kind="eignung" data-idx="' + idx + '">';
      html += '<td style="width:4.75rem">' + refInput(row.requirement_ref, 'criteria-in-ref') + '</td>';
      html += '<td><input class="form-input criteria-in-name" value="' + esc(row.name) + '" placeholder="Kriterium" /></td>';
      html += '<td><textarea class="form-input criteria-in-desc' + descClass(row.description) + '" rows="2" placeholder="Anforderungstext (Pflichtenheft)">' + esc(row.description) + '</textarea></td>';
      html += '<td style="width:3.5rem;text-align:center">K.O.</td>';
      html += '<td style="width:2.5rem"><button type="button" class="btn btn-ghost btn-sm criteria-del" title="Entfernen">×</button></td>';
      html += '</tr>';
      (row.children || []).forEach(function (ch, cidx) {
        html += '<tr class="criteria-row criteria-child-row" data-kind="eignung" data-idx="' + idx + '" data-cidx="' + cidx + '">';
        html += '<td style="padding-left:.5rem">' + refInput(ch.requirement_ref, 'criteria-in-cref', true) + '</td>';
        html += '<td style="padding-left:1.25rem"><input class="form-input criteria-in-cname" value="' + esc(ch.name) + '" placeholder="Unterfrage" /></td>';
        html += '<td><textarea class="form-input criteria-in-cdesc' + descClass(ch.description) + '" rows="2">' + esc(ch.description) + '</textarea></td>';
        html += '<td></td><td><button type="button" class="btn btn-ghost btn-sm criteria-del-child">×</button></td>';
        html += '</tr>';
      });
      html += '<tr class="criteria-add-child-row" data-kind="eignung" data-idx="' + idx + '"><td colspan="5"><button type="button" class="btn btn-ghost btn-sm criteria-add-child">+ Unterfrage</button></td></tr>';
    });
    tbody.innerHTML = html || '<tr><td colspan="5" class="text-muted" style="font-size:.78rem">Noch keine Eignungskriterien — Zeile hinzufügen oder KI erneut starten.</td></tr>';
  }

  function renderZuschlag() {
    const tbody = document.getElementById('criteria-zuschlag-body');
    if (!tbody) return;
    let html = '';
    state.zuschlag.forEach(function (row, idx) {
      html += '<tr class="criteria-row" data-kind="zuschlag" data-idx="' + idx + '">';
      html += '<td style="width:4.75rem">' + refInput(row.requirement_ref, 'criteria-in-ref') + '</td>';
      html += '<td><input class="form-input criteria-in-name" value="' + esc(row.name) + '" /></td>';
      html += '<td><textarea class="form-input criteria-in-desc' + descClass(row.description) + '" rows="2">' + esc(row.description) + '</textarea></td>';
      html += '<td style="width:4.5rem"><input class="form-input criteria-in-weight" type="number" step="1" min="0" max="100" value="' + esc(row.weight_pct) + '" /></td>';
      html += '<td style="width:3.5rem"><input class="form-input criteria-in-scale" type="number" min="1" max="100" value="' + esc(row.scale_max) + '" /></td>';
      html += '<td style="width:8rem"><select class="form-select criteria-in-phase">' + phaseOptions(row.ranking_phase) + '</select></td>';
      html += '<td style="width:3rem;text-align:center"><input type="checkbox" class="criteria-in-autoprice"' + (row.auto_price ? ' checked' : '') + ' title="Preis aus Preisblatt" /></td>';
      html += '<td style="width:2.5rem"><button type="button" class="btn btn-ghost btn-sm criteria-del">×</button></td>';
      html += '</tr>';
      (row.children || []).forEach(function (ch, cidx) {
        html += '<tr class="criteria-row criteria-child-row" data-kind="zuschlag" data-idx="' + idx + '" data-cidx="' + cidx + '">';
        html += '<td style="padding-left:.5rem">' + refInput(ch.requirement_ref, 'criteria-in-cref', true) + '</td>';
        html += '<td style="padding-left:1.25rem"><input class="form-input criteria-in-cname" value="' + esc(ch.name) + '" /></td>';
        html += '<td><textarea class="form-input criteria-in-cdesc' + descClass(ch.description) + '" rows="2">' + esc(ch.description) + '</textarea></td>';
        html += '<td colspan="4"></td><td><button type="button" class="btn btn-ghost btn-sm criteria-del-child">×</button></td>';
        html += '</tr>';
      });
      html += '<tr class="criteria-add-child-row" data-kind="zuschlag" data-idx="' + idx + '"><td colspan="8"><button type="button" class="btn btn-ghost btn-sm criteria-add-child">+ Unterfrage</button></td></tr>';
    });
    tbody.innerHTML = html || '<tr><td colspan="8" class="text-muted" style="font-size:.78rem">Noch keine Zuschlagskriterien.</td></tr>';
  }

  function renderAlerts(m) {
    const box = document.getElementById('criteria-alerts');
    if (!box) return;
    let html = '';
    if (m.missing_eignung) {
      html += '<div class="criteria-alert criteria-alert-critical"><strong>Keine Eignungskriterien</strong> — BöB/IVöB erwartet meist mindestens ein K.O.-Kriterium. Bitte Zeile hinzufügen, Vorgaben-Dokumente prüfen oder bewusst bestätigen.</div>';
    }
    if (!m.weight_ok && state.zuschlag.length) {
      html += '<div class="criteria-alert criteria-alert-critical"><strong>Zuschlags-Gewichtung: ' + m.weight_total + '%</strong> — erwartet ca. 100%. Werte in der Tabelle anpassen oder bewusst bestätigen.</div>';
    }
    if (m.empty_descriptions.length) {
      html += '<div class="criteria-alert criteria-alert-warn"><strong>Unvollständige Beschreibungen (' + m.empty_descriptions.length + ')</strong>: ' + esc(m.empty_descriptions.slice(0, 6).join(', ')) + (m.empty_descriptions.length > 6 ? ' …' : '') + '. Gelb markierte Felder bitte ergänzen.</div>';
    }
    (m.completeness || []).forEach(function (c) {
      if (c.complete) return;
      html += '<div class="criteria-alert criteria-alert-warn"><strong>' + esc(c.name) + '</strong>'
        + (c.ref ? ' (' + esc(c.ref) + ')' : '')
        + ': ' + c.found + ' von ' + c.expected + ' Einzelanforderungen erkannt</div>';
    });
    box.innerHTML = html;
    const confirmBlock = document.getElementById('criteria-confirm-block');
    if (confirmBlock) confirmBlock.hidden = !m.requires_confirm;
    const sumEl = document.getElementById('criteria-weight-sum');
    if (sumEl) {
      sumEl.textContent = 'Summe Gewichtung: ' + m.weight_total + '%';
      sumEl.className = 'criteria-weight-sum ' + (m.weight_ok ? 'is-ok' : 'is-bad');
    }
    const counts = document.getElementById('criteria-counts');
    if (counts) {
      let childN = 0;
      state.eignung.forEach(function (r) { childN += (r.children || []).length; });
      state.zuschlag.forEach(function (r) { childN += (r.children || []).length; });
      if (manageMode) {
        counts.textContent = m.eignung_count + ' Eignungs- und ' + m.zuschlag_count
          + ' Zuschlagskriterien, ' + childN + ' Unterfragen (speichern übernimmt Änderungen).';
      } else {
        counts.textContent = m.eignung_count + ' Eignungs- und ' + m.zuschlag_count
          + ' Zuschlagskriterien (bearbeitbar). Bestehende Namen im Projekt werden beim Übernehmen übersprungen.';
      }
    }
  }

  function readFromDom() {
    document.querySelectorAll('#criteria-eignung-body tr.criteria-row[data-kind="eignung"]:not(.criteria-child-row)').forEach(function (tr) {
      const idx = parseInt(tr.dataset.idx, 10);
      if (isNaN(idx) || !state.eignung[idx]) return;
      state.eignung[idx].name = tr.querySelector('.criteria-in-name')?.value || '';
      state.eignung[idx].description = tr.querySelector('.criteria-in-desc')?.value || '';
      state.eignung[idx].requirement_ref = tr.querySelector('.criteria-in-ref')?.value || '';
    });
    document.querySelectorAll('#criteria-eignung-body tr.criteria-child-row[data-kind="eignung"]').forEach(function (tr) {
      const idx = parseInt(tr.dataset.idx, 10);
      const cidx = parseInt(tr.dataset.cidx, 10);
      if (!state.eignung[idx] || !state.eignung[idx].children[cidx]) return;
      state.eignung[idx].children[cidx].name = tr.querySelector('.criteria-in-cname')?.value || '';
      state.eignung[idx].children[cidx].description = tr.querySelector('.criteria-in-cdesc')?.value || '';
      state.eignung[idx].children[cidx].requirement_ref = tr.querySelector('.criteria-in-cref')?.value || '';
    });
    document.querySelectorAll('#criteria-zuschlag-body tr.criteria-row[data-kind="zuschlag"]:not(.criteria-child-row)').forEach(function (tr) {
      const idx = parseInt(tr.dataset.idx, 10);
      if (isNaN(idx) || !state.zuschlag[idx]) return;
      state.zuschlag[idx].name = tr.querySelector('.criteria-in-name')?.value || '';
      state.zuschlag[idx].description = tr.querySelector('.criteria-in-desc')?.value || '';
      state.zuschlag[idx].requirement_ref = tr.querySelector('.criteria-in-ref')?.value || '';
      state.zuschlag[idx].weight_pct = parseFloat(tr.querySelector('.criteria-in-weight')?.value) || 0;
      state.zuschlag[idx].scale_max = parseInt(tr.querySelector('.criteria-in-scale')?.value, 10) || 10;
      state.zuschlag[idx].ranking_phase = parseInt(tr.querySelector('.criteria-in-phase')?.value, 10) || 1;
      state.zuschlag[idx].auto_price = !!tr.querySelector('.criteria-in-autoprice')?.checked;
    });
    document.querySelectorAll('#criteria-zuschlag-body tr.criteria-child-row[data-kind="zuschlag"]').forEach(function (tr) {
      const idx = parseInt(tr.dataset.idx, 10);
      const cidx = parseInt(tr.dataset.cidx, 10);
      if (!state.zuschlag[idx] || !state.zuschlag[idx].children[cidx]) return;
      state.zuschlag[idx].children[cidx].name = tr.querySelector('.criteria-in-cname')?.value || '';
      state.zuschlag[idx].children[cidx].description = tr.querySelector('.criteria-in-cdesc')?.value || '';
      state.zuschlag[idx].children[cidx].requirement_ref = tr.querySelector('.criteria-in-cref')?.value || '';
    });
  }

  function renderAll() {
    readFromDom();
    renderEignung();
    renderZuschlag();
    updateSummary();
  }

  function updateSummary() {
    const m = meta();
    renderAlerts(m);
    syncHidden();
  }

  root.addEventListener('input', function (e) {
    if (!e.target.closest('#criteria-preview-app')) return;
    if (e.target.matches('.criteria-in-desc, .criteria-in-cdesc')) {
      e.target.classList.toggle('criteria-desc-empty', !(e.target.value || '').trim());
    }
    readFromDom();
    updateSummary();
  });
  root.addEventListener('change', function (e) {
    if (!e.target.closest('#criteria-preview-app')) return;
    readFromDom();
    updateSummary();
  });

  root.addEventListener('click', function (e) {
    const t = e.target;
    if (t.id === 'criteria-add-eignung') {
      readFromDom();
      state.eignung.push(normEignung({}));
      renderAll();
    } else if (t.id === 'criteria-add-zuschlag') {
      readFromDom();
      state.zuschlag.push(normZuschlag({ weight_pct: 0, ranking_phase: 1 }));
      renderAll();
    } else if (t.classList.contains('criteria-del')) {
      readFromDom();
      const tr = t.closest('tr');
      const kind = tr.dataset.kind;
      const idx = parseInt(tr.dataset.idx, 10);
      if (kind === 'eignung') {
        trackDelete(state.eignung[idx]);
        (state.eignung[idx].children || []).forEach(trackDelete);
        state.eignung.splice(idx, 1);
      } else {
        trackDelete(state.zuschlag[idx]);
        (state.zuschlag[idx].children || []).forEach(trackDelete);
        state.zuschlag.splice(idx, 1);
      }
      renderAll();
    } else if (t.classList.contains('criteria-del-child')) {
      readFromDom();
      const tr = t.closest('tr');
      const idx = parseInt(tr.dataset.idx, 10);
      const cidx = parseInt(tr.dataset.cidx, 10);
      if (tr.dataset.kind === 'eignung') {
        trackDelete(state.eignung[idx].children[cidx]);
        state.eignung[idx].children.splice(cidx, 1);
      } else {
        trackDelete(state.zuschlag[idx].children[cidx]);
        state.zuschlag[idx].children.splice(cidx, 1);
      }
      renderAll();
    } else if (t.classList.contains('criteria-add-child')) {
      readFromDom();
      const tr = t.closest('tr');
      const idx = parseInt(tr.dataset.idx, 10);
      if (tr.dataset.kind === 'eignung') {
        state.eignung[idx].children.push({ name: '', description: '', scale_max: 1 });
      } else {
        state.zuschlag[idx].children.push({ name: '', description: '', scale_max: 10 });
      }
      renderAll();
    }
  });

  const form = document.getElementById('criteria-apply-form');
  if (form) {
    form.addEventListener('submit', function (e) {
      readFromDom();
      syncHidden();
      if (manageMode) return;
      const m = meta();
      const confirm = document.getElementById('criteria-confirm-apply');
      if (m.requires_confirm && confirm && !confirm.checked) {
        e.preventDefault();
        const err = document.getElementById('criteria-apply-error');
        if (err) {
          err.hidden = false;
          err.textContent = 'Bitte Hinweise oben prüfen und die Bestätigung aktivieren — oder Daten in den Tabellen korrigieren.';
        }
        document.getElementById('criteria-alerts')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  }

  loadInitial();
  renderAll();
})();
