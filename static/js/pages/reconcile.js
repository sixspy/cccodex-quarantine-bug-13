import { api } from '../api.js';

export async function renderReconcile(el) {
  const data = await api('/api/reports');
  const tp = await api('/api/timeout-promotions/summary');
  el.innerHTML = `
    <div class="card">
      <h2>隔离对账</h2>
      <div class="row"><button id="run">生成对账报告</button></div>
      <table>
        <thead><tr><th>时间</th><th>摘要</th><th>待复核</th><th>放行</th><th>回炉</th><th>废弃</th><th>错位</th></tr></thead>
        <tbody>
          ${(data.items || []).map((r) => `
            <tr>
              <td>${r.created_at}</td>
              <td>${r.summary}</td>
              <td>${r.open_rows}</td>
              <td>${r.released}</td>
              <td>${r.reworking}</td>
              <td>${r.abandoned}</td>
              <td><span class="chip ${r.mismatch ? 'bad' : 'ok'}">${r.mismatch}</span></td>
            </tr>`).join('') || '<tr><td colspan="7" class="muted">暂无报告</td></tr>'}
        </tbody>
      </table>
    </div>
    <div class="card">
      <h2>超时晋升对账（${tp.mode === 'snapshot' ? '甲路·不可变快照' : tp.mode}）</h2>
      <p class="muted">${tp.summary_text}</p>
      <div class="stat-strip">
        <div class="stat"><b>${tp.timeout_total}</b><span class="muted">超时档（${tp.timeout_stage}）</span></div>
        <div class="stat"><b>${tp.promoted}</b><span class="muted">已晋升落点</span></div>
        <div class="stat"><b class="${tp.hanging ? '' : ''}">${tp.hanging}</b><span class="muted">悬挂未晋升</span></div>
        <div class="stat">
          <b><span class="chip ${tp.gate_open ? 'ok' : 'bad'}">${tp.gate_open ? '闸门开' : '闸门拦'}</span></b>
          <span class="muted">徽标/超时档/落点拧花即拦清洗</span>
        </div>
      </div>
      ${(tp.hanging_jobs || []).length ? `
        <table>
          <thead><tr><th>悬挂任务</th><th>条目</th><th>处置</th></tr></thead>
          <tbody>
            ${tp.hanging_jobs.map((j) => `
              <tr>
                <td>${j.job_code}</td>
                <td>${j.item_key}</td>
                <td><button data-promote="${j.id}">超时晋升</button></td>
              </tr>`).join('')}
          </tbody>
        </table>` : '<p class="muted">无悬挂脏件。</p>'}
    </div>`;
  el.querySelector('#run').onclick = async () => {
    await api('/api/reports/run', { method: 'POST', body: '{}' });
    await renderReconcile(el);
  };
  el.querySelectorAll('[data-promote]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const out = await api('/api/timeout-promotions/commit', {
          method: 'POST', body: JSON.stringify({ job_id: Number(btn.dataset.promote) }),
        });
        alert(`晋升已写定：快照#${out.snapshot_id}`);
      } catch (e) { alert(e.message); }
      await renderReconcile(el);
    };
  });
}
