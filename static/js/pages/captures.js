import { api } from '../api.js';

export async function renderCaptures(el) {
  const data = await api('/api/batches');
  const bins = await api('/api/bins');
  const openBin = (bins.items || []).find((b) => b.status === '开放');
  el.innerHTML = `
    <div class="card">
      <h2>抓取批次</h2>
      <div class="row">
        <input id="src" placeholder="来源名" />
        <input id="note" placeholder="备注" />
        <button id="create">新建批次</button>
      </div>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>批号</th><th>来源</th><th>状态</th><th>条目/坏件</th><th>操作</th></tr></thead>
        <tbody>
          ${(data.items || []).map((b) => `
            <tr>
              <td>${b.batch_code}</td>
              <td>${b.source_name}</td>
              <td><span class="chip">${b.status}</span></td>
              <td>${b.item_count} / <span class="chip bad">${b.bad_count}</span></td>
              <td class="row">
                <button data-open="${b.id}">详情</button>
                <button data-close="${b.id}">关闭</button>
                ${openBin ? `<button data-auto="${b.id}">坏件入舱</button>` : ''}
              </td>
            </tr>`).join('')}
        </tbody>
      </table>
      <div id="detail" class="grid" style="margin-top:12px"></div>
    </div>`;
  el.querySelector('#create').onclick = async () => {
    await api('/api/batches', {
      method: 'POST',
      body: JSON.stringify({
        source_name: el.querySelector('#src').value,
        note: el.querySelector('#note').value,
      }),
    });
    await renderCaptures(el);
  };
  el.querySelectorAll('[data-close]').forEach((btn) => {
    btn.onclick = async () => {
      await api(`/api/batches/${btn.dataset.close}/close`, { method: 'POST', body: '{}' });
      await renderCaptures(el);
    };
  });
  el.querySelectorAll('[data-auto]').forEach((btn) => {
    btn.onclick = async () => {
      if (!openBin) return;
      await api('/api/quarantine/auto-enter', {
        method: 'POST',
        body: JSON.stringify({ batch_id: Number(btn.dataset.auto), bin_id: openBin.id }),
      });
      await renderCaptures(el);
    };
  });
  el.querySelectorAll('[data-open]').forEach((btn) => {
    btn.onclick = async () => {
      const d = await api(`/api/batches/${btn.dataset.open}`);
      const box = el.querySelector('#detail');
      box.innerHTML = `
        <h3>${d.batch_code} 条目</h3>
        <div class="row">
          <input id="ikey" placeholder="条目键" />
          <select id="iq">
            <option>合格</option><option>缺字段</option><option>编码空</option><option>重复键</option>
          </select>
          <button id="addItem">追加条目</button>
        </div>
        <table>
          <thead><tr><th>键</th><th>质量</th><th>状态</th><th>超时档</th><th>晋升徽标</th><th>晋升落点</th></tr></thead>
          <tbody>
            ${(d.items || []).map((i) => `
              <tr>
                <td>${i.item_key}</td>
                <td><span class="chip ${i.quality === '合格' ? 'ok' : 'bad'}">${i.quality}</span></td>
                <td>${i.status}</td>
                <td>${i.timeout_stage ? `<span class="chip bad">${i.timeout_stage}</span>` : '<span class="muted">—</span>'}</td>
                <td>${i.promotion_badge === '已晋升'
                  ? `<span class="chip ok">${i.promotion_badge}</span>`
                  : (i.promotion_badge === '未晋升' ? `<span class="chip bad">${i.promotion_badge}</span>` : '<span class="muted">—</span>')}</td>
                <td>${i.promotion_snapshot_id
                  ? `<button class="ghost" data-snap="${i.promotion_snapshot_id}">快照#${i.promotion_snapshot_id}</button>`
                  : '<span class="muted">无落点</span>'}</td>
              </tr>`).join('')}
          </tbody>
        </table>`;
      box.querySelectorAll('[data-snap]').forEach((btn) => {
        btn.onclick = async () => {
          const snap = await api(`/api/timeout-promotions/snapshots/${btn.dataset.snap}`);
          alert(
            `晋升快照 #${snap.snapshot_id}\n令牌：${snap.promote_token}\n` +
            `指纹：${snap.fingerprint}\n任务：${snap.snapshot_job_code}\n` +
            `当刻状态：${snap.snapshot_job_status}（只读，不可回刷）`
          );
        };
      });
      box.querySelector('#addItem').onclick = async () => {
        await api(`/api/batches/${d.id}/items`, {
          method: 'POST',
          body: JSON.stringify({
            item_key: box.querySelector('#ikey').value,
            quality: box.querySelector('#iq').value,
            payload: '{}',
          }),
        });
        btn.click();
      };
    };
  });
}
