"""Discover, plan, download and ingest CHD history from the MDRAP terminal."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from chd import CHDClient, CHDError, DATA_TYPES, EXCHANGES, HistoricalRequest


def add_historical_parser(subparsers):
    parser = subparsers.add_parser('historical', aliases=['history', 'chd'],
                                  help='CHD historical data: discover, download, ingest and replay')
    actions = parser.add_subparsers(dest='historical_action', required=True)
    providers = actions.add_parser('providers', help='List CHD exchanges and datasets (offline)')
    providers.set_defaults(func=cmd_historical)
    symbols = actions.add_parser('symbols', help='Discover exact historical exchange symbols')
    symbols.add_argument('--exchange', required=True, choices=EXCHANGES)
    symbols.add_argument('--data-type', choices=DATA_TYPES)
    symbols.add_argument('--json', action='store_true', help='Machine-readable output')
    symbols.set_defaults(func=cmd_historical)
    for action in ('plan', 'download', 'ingest'):
        p = actions.add_parser(action, help={
            'plan': 'Show UTC hourly files without downloading',
            'download': 'Cache verified native Parquet files and report completeness',
            'ingest': 'Create a new run with canonical SQLite data, raw replay archive and manifest',
        }[action])
        p.add_argument('--exchange', required=True, choices=EXCHANGES)
        p.add_argument('--symbol', required=True, help='Exact CHD symbol; no automatic currency/market conversion')
        p.add_argument('--data-type', default='trades', choices=('trades', 'orderbook') if action == 'ingest' else DATA_TYPES)
        p.add_argument('--start', required=True, help='Inclusive UTC date or ISO timestamp with timezone')
        p.add_argument('--end', required=True, help='Exclusive UTC date or ISO timestamp with timezone')
        p.add_argument('--json', action='store_true', help='Machine-readable output')
        if action != 'plan':
            p.add_argument('--cache-dir', default='data/chd-cache')
            p.add_argument('--offline', action='store_true', help='Require verified cached files; no network requests')
            p.add_argument('--timeout', type=float, default=60)
            p.add_argument('--retries', type=int, default=3)
        if action == 'download':
            p.add_argument('--allow-missing', action='store_true', help='Report 404 hours as gaps; never marks a partial download complete')
            p.add_argument('--manifest', help='Write manifest to a new JSON file')
        if action == 'ingest':
            p.add_argument('--output', required=True, help='New run directory (existing paths are refused)')
            p.add_argument('--book-start', help='Read earlier hours for orderbook snapshot warmup; output still begins at --start')
            p.add_argument('--timestamp-unit', choices=('auto', 's', 'ms', 'us', 'ns'), default='auto')
        p.set_defaults(func=cmd_historical)
    return parser


def cmd_historical(args):
    try:
        _run(args)
    except (CHDError, ValueError, OSError) as exc:
        print(f'[CHD] {exc}', file=sys.stderr)
        raise SystemExit(1) from None


def _run(args):
    action = args.historical_action
    if action == 'providers':
        from term import Console, Table
        table = Table(title='CryptoHFTData · Historical market data')
        table.add_column('Exchange / market')
        table.add_column('Native datasets')
        for exchange in EXCHANGES:
            types = DATA_TYPES[:3] if exchange.endswith('_spot') else DATA_TYPES
            table.add_row(exchange, ', '.join(types))
        Console().print(table)
        print('Discover symbols to check dataset coverage. UTC intervals use [start, end).')
        print('Optional credentials: CRYPTOHFTDATA_API_KEY. Install: pip install "mdrap[chd]"')
        return
    client = CHDClient(cache_dir=getattr(args, 'cache_dir', 'data/chd-cache'),
                       timeout=getattr(args, 'timeout', 60), max_retries=getattr(args, 'retries', 3),
                       offline=getattr(args, 'offline', False))
    if action == 'symbols':
        values = client.list_symbols(args.exchange, args.data_type)
        if args.json:
            print(json.dumps(dict(exchange=args.exchange, data_type=args.data_type,
                                  count=len(values), symbols=values), indent=2))
        else:
            print(f'CHD · {args.exchange} · {len(values):,} historical symbols')
            print('\n'.join(values))
        return
    request = HistoricalRequest(args.exchange, args.symbol, args.data_type, args.start, args.end)
    if action == 'plan':
        result = dict(provider='chd', request=request.as_dict(),
                      files=[p.key for p in request.partitions()])
        result['hours'] = len(result['files'])
    elif action == 'download':
        if args.manifest and Path(args.manifest).exists():
            raise FileExistsError(f'Manifest already exists: {args.manifest}')
        result = client.download(request, allow_missing=args.allow_missing)
        if args.manifest:
            with open(args.manifest, 'x', encoding='utf-8') as f:
                json.dump(result, f, indent=2)
                f.write('\n')
    else:
        from chd_history import ingest_history
        if not args.json:
            print(f'CHD · {args.exchange} / {args.symbol} · {args.data_type}', file=sys.stderr)
            print('Verifying hourly files and building historical run…', file=sys.stderr)
        result = ingest_history(client, request, args.output, timestamp_unit=args.timestamp_unit, book_start=args.book_start)
    if args.json:
        print(json.dumps(result, indent=2))
    elif action == 'plan':
        print(f'CHD · {result["hours"]:,} hourly files · no downloads made')
        print('\n'.join(result['files']))
    else:
        from term import Console, Table
        table = Table(title='CHD · Historical import' if action == 'ingest' else 'CHD · Historical download')
        table.add_column('Measure')
        table.add_column('Result')
        table.add_row('Market', f'{request.exchange} / {request.symbol}')
        table.add_row('Dataset', request.data_type)
        table.add_row('UTC interval', f'{request.start.isoformat()} → {request.end.isoformat()} (exclusive)')
        table.add_row('Coverage', 'Complete' if result['complete'] else 'INCOMPLETE')
        table.add_row('Verified hours', str(len(result['files'])))
        table.add_row('Missing hours', str(len(result['missing'])))
        table.add_row('Native rows in files', f'{sum(f["rows"] for f in result["files"]):,}')
        if action == 'ingest':
            table.add_row('Pipeline events', f'{result["events"]:,}')
            table.add_row('Output', str(Path(args.output).resolve()))
        Console().print(table)
        for key in result['missing']:
            print(f'MISSING {key}')
        if action == 'ingest':
            print(f'Database: {Path(args.output) / "mdrap.db"}')
            print(f'Replay archive: {Path(args.output) / "raw"}')
            print(f'Provenance: {Path(args.output) / "manifest.json"}')
    if result.get('complete') is False:
        raise SystemExit(2)
