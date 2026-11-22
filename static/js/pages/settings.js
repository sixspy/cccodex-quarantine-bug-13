import { api } from '../api.js';

export async function renderSettings(el) {
  const data = await api('/api/settings');
  el.innerHTML = `
    <div class="card">
      <h2>台参数</h2>
      <table>
        <thead><tr><th>项</th><th>说明</th><th>值</th><th></th></tr></thead>
        <tbody>
          ${(data.items || []).map((s) => `
            <tr>
              <td>${s.key}</td>
              <td>${s.label}</td>
              <td><input data-key="${s.key}" value="${s.value}" /></td>
              <td><button data-save="${s.key}">保存</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  el.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      const input = el.querySelector(`input[data-key="${btn.dataset.save}"]`);
      await api('/api/settings/update', {
        method: 'POST',
        body: JSON.stringify({ key: btn.dataset.save, value: input.value }),
      });
      await renderSettings(el);
    };
  });

  // 超时晋升口径：只展甲路（不可变快照），不出现乙路账册/双写任何入口
  const spec = await api('/api/timeout-promotions/settings');
  el.innerHTML += `<div class="card" style="margin-top:1rem">
    <h2>超时晋升口径</h2>
    <div class="stat-strip">
      <div class="stat"><b>${spec.mode}</b><span class="muted">${spec.mode_label}</span></div>
      <div class="stat"><b>${spec.read_ref}</b><span class="muted">唯一读轨（只读快照表）</span></div>
      <div class="stat"><b>${spec.timeout_stage}</b><span class="muted">回炉侧超时档</span></div>
      <div class="stat"><b>${spec.promoted_badge}</b><span class="muted">详情侧晋升徽标</span></div>
    </div>
    <p class="muted">超时当刻任务键与晋升指纹一次写定，快照只读、拒绝回刷；
      超时阈值 ${spec.timeout_policy.rework_timeout_seconds} 秒，
      回炉最大次数 ${spec.timeout_policy.rework_max_attempt}。</p>
  </div>`;
}
