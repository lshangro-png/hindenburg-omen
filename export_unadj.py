"""
export_unadj.py —— 生成"不复权价 + 不过滤可交易域"这一实验性口径的数据,
只给参数实验室(lab.py)用, 不进 dashboard。

背景: 多轮排查(可交易域/条件①口径/日涨跌幅/盘中high-low)都无法复现某些
公开复现页面在 2014-07/2015-06 附近标出的信号, 最后确认差异在于:
  ① 用不复权价算 52 周新高新低(不复权价在除权除息日的断崖式下跌会被
     误判成真实新低, 2014-2015 恰是高送转最密集的年份)
  ② 不做可交易域过滤(不剔 ST/停牌)
两者叠加(下文称"D组")才能让 2014-07-09/2015-06-29
两天的条件②都触发, 单独任何一个口径都不够。

这不是本项目认可的口径 —— 不复权跳空不是真实回撤, 用它判断"市场是否
分化"混淆了公司行为和市场行为。放进 lab 只是为了让你能亲眼对比"这样
改口径确实能复现出那些公开页面的信号", 不建议当作正式结论使用, 所以
不进 dashboard、也不算进 STRATEGIES。

用法:
  python export_unadj.py                    # 默认 data 目录, 中证全指
  python export_unadj.py data/data --start 2011-08-02 --end 2026-08-07

跑完会在 output/export_ashare_unadj.json 落一份和正常 export 同结构的
文件。lab.py 是按目录里所有 export_*.json 文件生成切换按钮的(不看
STRATEGIES 字典), 所以这个文件会自动出现在 lab.html 里、但不会出现在
dashboard.html 里(dashboard 只认 STRATEGIES 里登记过的几个策略键)。

跑一次之后同一区间会缓存 <数据目录>/intraday_cache/unadj_close_*.pkl,
之后重跑不再消耗聚宽配额。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))


def fetch_unadj_close(data_dir: Path, codes, start: str, end: str) -> pd.DataFrame:
    """
    从聚宽拉不复权收盘价, 落盘缓存, 重跑不重复消耗配额。

    坏代码隔离复用 jq_source._isolate_bad_codes: 聚宽对批量请求里任何一个
    无效代码(老代码改名/退市多年很常见)都是整批直接报错, 不隔离的话随便
    一个有问题就让整次拉取全军覆没。
    """
    cache = data_dir / 'intraday_cache' / f'unadj_close_{start}_{end}.pkl'
    if cache.exists():
        print(f'命中本地缓存 {cache.name}, 跳过拉取')
        return pd.read_pickle(cache)

    import jq_source
    jq = jq_source.jq_auth()
    jq_codes = [jq_source.to_jq_code(c) for c in codes]
    fields = ['close']

    print(f'先隔离聚宽认不出的代码({len(jq_codes)} 只)…')
    good, bad = jq_source._isolate_bad_codes(jq, jq_codes, end, fields)
    if bad:
        print(f'  剔除 {len(bad)} 只无效代码(如 {[c for c, _ in bad[:3]]})')

    print(f'从聚宽拉 {len(good)} 只股票不复权收盘价 {start}~{end} ...')
    frames = []
    BATCH = 2000
    for i in range(0, len(good), BATCH):
        batch = good[i:i + BATCH]
        df = jq.get_price(batch, start_date=start, end_date=end, frequency='daily',
                          fields=fields, skip_paused=False, fq=None, panel=False)
        if df is not None and len(df):
            frames.append(df)
        print(f'  {min(i+BATCH, len(good))}/{len(good)}')
    if not frames:
        raise RuntimeError('聚宽没有返回任何数据')
    raw = pd.concat(frames)
    wide = raw.pivot(index='time', columns='code', values='close')
    wide.columns = [jq_source.from_jq_code(c) for c in wide.columns]
    wide.index = pd.to_datetime(wide.index)
    wide = wide.reindex(columns=codes).astype('float32')

    cache.parent.mkdir(parents=True, exist_ok=True)
    wide.to_pickle(cache)
    print(f'已缓存到 {cache.name}, 下次运行直接读本地')
    return wide


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('data_dir', nargs='?', default='data')
    ap.add_argument('--strategy', default='ashare',
                    help='借用哪个策略的基准指数/参数, 默认 ashare(中证全指)')
    ap.add_argument('--start', default=None)
    ap.add_argument('--end', default=None)
    ap.add_argument('--out-dir', default=None, help='默认 config.OUT_DIR')
    a = ap.parse_args()

    import config
    config.DATA_DIR = a.data_dir
    a.out_dir = a.out_dir or config.OUT_DIR
    from data import Panel
    import hindenburg as H

    if a.strategy not in config.STRATEGIES:
        raise SystemExit(f'--strategy 只能是 {list(config.STRATEGIES)}')
    base = config.STRATEGIES[a.strategy]
    p = base.params
    start = a.start or base.start or config.START
    # 不传 state_rule: 就是要"不过滤可交易域"。注意 Panel 的列过滤仍然生效
    # (剔非股票代码 + 默认剔北交所), 只是不再按停牌/上市天数/ST 筛。
    panel = Panel()
    end = a.end or H._end_date(panel)

    print(f'借用策略: {a.strategy} ({base.name})  基准: {base.benchmark}  '
          f'区间: {start} ~ {end}')

    codes = list(panel.prices.columns)
    unadj = fetch_unadj_close(Path(a.data_dir), codes, start, end)

    # 可交易域不过滤: 只要求当天有不复权价, 不剔 ST/停牌, 这正是要
    # 对照的"D组"口径。
    valid = unadj.notna()
    df = H.new_high_low(unadj, valid, p.hl_window, p.hl_min_periods)
    df['mcclellan'] = H.mcclellan(unadj, valid, p.ema_short, p.ema_long, p.mc_norm)
    df = df.loc[start:end]

    store = H._make_store()
    pre_start = (pd.Timestamp(start)
                - pd.Timedelta(days=int(config.INDEX_PREHEAT * 1.6))).strftime('%Y-%m-%d')
    index_full = store.get(base.benchmark, pre_start, end).sort_index()
    index_full = index_full[~index_full.index.duplicated(keep='last')]
    df['index_close'] = index_full.reindex(df.index).ffill()
    first = df['index_close'].first_valid_index()
    if first is not None and first > df.index[0]:
        df = df.loc[first:]

    df = H.build_signals(df, p, index_full=index_full)
    cl = H.build_clusters(df, p)
    cl = H.evaluate(df, cl, p)

    class _FakeStrategy:
        name = f'{base.name}(不复权+不过滤·实验对照)'
        benchmark = base.benchmark
        state_rule = '不过滤(实验组)'
        params = p

    fake_strategy = _FakeStrategy()

    class _FakeOmen:
        def __init__(self, s, stats, index_source, index_full):
            self.s = s
            self.stats = stats
            self.index_source = index_source
            self.index_full = index_full

    omen = _FakeOmen(fake_strategy, df, base.name, index_full)
    payload = H.export_payload(omen, cl)

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_path = out / 'export_ashare_unadj.json'
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    print(f'\n已导出 {out_path} ({out_path.stat().st_size/1024:.0f} KB)')
    print(f'跑 `python lab.py {a.out_dir}` 就能在参数实验室的切换栏里看到这个对照组了。')
    print('(不会出现在 dashboard.html —— 那边只认 STRATEGIES 里登记过的策略)')


if __name__ == '__main__':
    main()
