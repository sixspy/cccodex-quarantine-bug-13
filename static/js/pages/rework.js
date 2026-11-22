import { api } from '../api.js';

export async function renderRework(el) {
  const data = await api('/api/rework/jobs');
  el.innerHTML = `
    <div class="card">
      <h2>回炉队列</h2>
      <table>
        <thead><tr><th>任务</th><th>条目</th><th>舱</th><th>状态</th><th>超时档</th><th>次数</th><th>晋升落点</th><th>操作</th></tr></thead>
        <tbody>
          ${(data.items || []).map((j) => `
            <tr>
              <td>${j.job_code}</td>
              <td>${j.item_key}</td>
              <td>${j.bin_code}</td>
              <td><span class="chip">${j.status}</span></td>
              <td>${j.timeout_stage ? `<span class="chip bad">${j.timeout_stage}</span>` : '<span class="muted">—</span>'}</td>
              <td>${j.attempt}</td>
              <td>${j.promotion_snapshot_id
                ? `<button class="ghost" data-snap="${j.promotion_snapshot_id}">快照#${j.promotion_snapshot_id}</button>`
                : '<span class="muted">无落点</span>'}</td>
              <td class="row">
                ${j.status === '执行中' ? `<button data-timeout="${j.id}">判超时</button>` : ''}
                ${j.timeout_stage && !j.promotion_snapshot_id ? `<button data-promote="${j.id}">超时晋升</button>` : ''}
                ${['排队','失败'].includes(j.status) && !j.timeout_stage ? `
                  <button data-ok="${j.id}">执行成功</button>
                  <button data-bad="${j.id}">执行失败</button>
                ` : (j.message || '')}
              </td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  const refresh = () => renderRework(el);
  el.querySelectorAll('[data-ok]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`/api/rework/jobs/${btn.dataset.ok}/run`, {
          method: 'POST', body: JSON.stringify({ succeed: true }),
        });
      } catch (e) { alert(e.message); }
      await refresh();
    };
  });
  el.querySelectorAll('[data-bad]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`/api/rework/jobs/${btn.dataset.bad}/run`, {
          method: 'POST', body: JSON.stringify({ succeed: false }),
        });
      } catch (e) { alert(e.message); }
      await refresh();
    };
  });
  el.querySelectorAll('[data-timeout]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`/api/rework/jobs/${btn.dataset.timeout}/timeout`, {
          method: 'POST', body: '{}',
        });
      } catch (e) { alert(e.message); }
      await refresh();
    };
  });
  el.querySelectorAll('[data-promote]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const out = await api('/api/timeout-promotions/commit', {
          method: 'POST', body: JSON.stringify({ job_id: Number(btn.dataset.promote) }),
        });
        alert(`晋升已写定：快照#${out.snapshot_id}\n令牌 ${out.promote_token}`);
      } catch (e) { alert(e.message); }
      await refresh();
    };
  });
  el.querySelectorAll('[data-snap]').forEach((btn) => {
    btn.onclick = async () => {
      const snap = await api(`/api/timeout-promotions/snapshots/${btn.dataset.snap}`);
      alert(
        `晋升快照 #${snap.snapshot_id}\n令牌：${snap.promote_token}\n` +
        `指纹：${snap.fingerprint}\n任务：${snap.snapshot_job_code}\n` +
        `当刻状态：${snap.snapshot_job_status}（只读，不可回刷）`
      );
    };
  });
}
