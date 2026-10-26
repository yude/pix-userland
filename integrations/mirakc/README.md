# PIX-DT295 と mirakc の接続

## 現状と構成

`pixd` が USB デバイスを一つだけ所有し、mirakc のチューナーコマンドから `pix-mirakc-tuner` → 現行の `pix-ts` → pixd の Unix socket へ接続する。`pix-ts-legacy` はこの構成に使わない。GR は既存 CLI だけでも `pix-ts --instance dt295 --channel T27 --time - --output -` と設定できる。薄いアダプターは mirakc の `GR/BS/CS`、チャンネル、期間を検査し、DT295 が扱えない衛星 slot を早期に拒否する。変換後は `exec pix-ts` するため追加のTSバッファーや別daemonはない。

`pixd` と `pix-ts` の `--instance dt295` および `--runtime-dir /run/pixd` は同じ値にする。Core は `/run/pixd/pix-userland/v1/dt295/` の制御・ストリームソケットを使う。`--group` は両側で指定し、共有グループで通信する。別インスタンスを使う場合は両方を変更する。1台の物理チューナーを mirakc の複数 `tuners` エントリーとして登録しない。Core の lease が並列利用を拒否し、STOP/RELEASE で再利用する。

Linux x86_64でビルドと合成USB fixture の IPC 経路を確認した。**DT295実機によるこの統合の受信、視聴、録画、EPG、USB解放は未確認**。機器固有の `dt295.assets` は合成していない。

## チャンネル指定

| mirakc `type` | `channel` の例 | pix-ts へ渡す値 | 備考 |
|---|---|---|---|
| GR | `T27` または `27` | `--system isdb-t --channel T27` | 物理 UHF T13〜T62 |
| BS | `BS01_0` | `--system isdb-s --channel BS01 --slot 0` | BS奇数01〜23、slot 0〜7 |
| BS | `BS01@16400` | `--channel BS01 --tsid 16400` | `@TSID` はこのアダプターの表記 |
| CS | `CS22_0` | `--channel CS22 --slot 0` | CS偶数02〜24、slotを明示 |
| CS | `CS22@29024` | `--channel CS22 --tsid 29024` | 実機未検証の指定例 |

`--slot` / `--tsid` をアダプターの直接CLI引数として渡すこともできる。`BS01` 単独、`CS22` 単独、slot 8以上、重複選択は拒否する。Core は `CS22` 単独を slot 11 と解釈するが、DT295 backend の slot は0〜7なので、この曖昧な指定を通さない。Core がチャンネル表記からBS/CSのIF周波数へ変換するため、アダプターで周波数表を複製しない。BS slot は demod が返す候補を参照し、TSID指定は明示値を選ぶ。衛星はBS/CSとも `isdb-s`。衛星用の新しいレジスター表を含む正規のDT295 bundleが必要である。LNB給電機能は現行PIX CLIにないため、アンテナ設備からの給電条件を別途確認する。

mirakc は現在 `{{{duration}}}` に `-` を与えるが、アダプターは正の十進秒数も `pix-ts --time` に渡す。TSは標準出力のみ、診断は標準エラー出力のみ。アダプターはシェル評価をせず、入力を検査して引数を個別に渡す。ただし mirakc の `command` テンプレート展開自体はシェルで実行されるため、管理者が書く `channels[].channel` に任意のシェル文字列を入れない。

## Linux ホストへの導入

Debian/Ubuntuでは C/C++、CMake、pkg-config、`libusb-1.0-0-dev`、Python 3 を用意する。公開Coreの固定リビジョンは `core-integration/dtv-userland-core.revision` を参照する。実機用データなしでビルドと自動テストができる。

```sh
cmake -S core-integration -B build/core-integration
cmake --build build/core-integration -j 4
ctest --test-dir build/core-integration --output-on-failure
python3 -m unittest discover -s integrations/mirakc/tests -v
sudo install -m 0755 build/core-integration/pixd build/core-integration/pixctl \
  build/core-integration/pix-ts integrations/mirakc/pix-mirakc-tuner /usr/local/bin/
```

実機資産は [DT295資料](../../docs/dt295.md) に従って機器所有者が正規の入力から準備する。鍵、IV、カード情報、TS録画はGitへ追加しない。`pixd` を起動する前に `pixd --list-json` で `PIX-DT295` と固有の selector を確認する。udev では `SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="06b8", ATTR{idProduct}=="103b", MODE="0660", GROUP="pixd"` のように対象機器だけのアクセスを与える。ruleを反映し再接続した後、`pixd` 実行ユーザーが `/dev/bus/usb` の当該ノードを開けることを確認する。

[`pixd.service.example`](pixd.service.example) は systemd の起動例。`REPLACE_SERIAL` と assets のパスを実機に合わせ、`pixd` 用ユーザー・グループを作成してからインストールする。`RuntimeDirectoryMode=0770` と `--group` は同じ共有ソケット方式の設定である。`pixctl --instance dt295 --runtime-dir /run/pixd --group status` の `context.state=ready` と receiver `state=free` を確認してから mirakc を起動する。USB抜き差し後の自動復旧は保証していない。pixd のプロセス状態、socket、実際の新規取得を確認して再起動を判断する。

[`config.example.yml`](config.example.yml) はGRのみの運用例。T27は実際の地域の物理チャンネルに置き換える。mirakc の `{{{channel_type}}}`、`{{{channel}}}`、`{{{duration}}}` は公式仕様どおりの tuner command 変数である。デスクランブルの経路と実機での判定は次節に記載する。

## デスクランブルと本体内カード

PIX-DT295では本体に挿したB-CASカードを使う構成が第一候補である。現行 `pixd` のDT295 receiverは、受信開始時に本体内カードを初期化・activateし、受け取ったTSを `dt295_decode_packet()` に通してからIPCへ渡す。[receiver実装](../../pix-dt295-receiver-adapter.cpp) はカード処理に失敗すると `NO_CARD` を返す。TS処理は機器由来のAES転送暗号を解き、対応するpacketのscrambling bitsをクリアする。以前のDT295実機検証では地上波T27の保存TSについてscramble=0を観測しているが、**今回のmirakc統合・Linux・BS/CS・有料サービスでの復号成功は未検証**である。`decoded: true` は復号済みPESをチューナーが出すという[mirakcの契約](https://github.com/mirakc/mirakc/blob/main/docs/config.md#tuners)に合わせた設定で、復号成功そのものを保証するスイッチではない。

参照した[既存のdtv-tuner構成](https://github.com/yudejp/nrt1sh1.tun.y2e.org/tree/main/dtv-tuner)は、別機種の生TSを `decode-filter` → B25サーバー → PC/SC外付けカードリーダーへ通す。PIX-DT295の内蔵カードはホストのPC/SC readerとして公開されず、現行 `pixctl` の `card-atr`/`card-apdu`/`card-reset` も未対応である。このため、そのB25/pcscd proxyをDT295内蔵カードへ接続することはできない。DT295のTSを外部B25にも通す二重処理は行わず、両設定例は `decoded: true` と空の `filters.decode-filter.command` を明示する。別の外付けカードを使うB25構成を混ぜる場合も、現行DT295出力が外部B25の入力として使えることは確認できていない。

実機では短時間のTSを保存し、188バイト同期とTS headerのscrambling_controlを検査する。受信中ならホストから `pixctl --instance dt295 --runtime-dir /run/pixd --group card-status` で本体内カードの `present=yes` を確認できる場合がある。receiverを開いていない待機中や処理の競合時は `present=unknown` が返り得るため、これはカード不在の判定には使わない。ヘッダー検査だけでは映像・音声の復号成功を証明できないので、最後にサービス別のPESを実際に再生・短時間録画して確認する。`NO_CARD`、スクランブルが残る、サービスごとにAVが欠ける場合はその段階を切り分け、`decoded` を盲目的に変更しない。生TS・鍵・カード識別情報をissueやログへ添付しない。

mirakc の標準 `service-filter` と `program-filter` を使い、外部 `decode-filter` は実行しない。サービス一覧にはPAT/SDT/NIT、サービスストリームにはPMT/AV、EPGにはEITと時刻情報の安定供給が必要で、単にTSが出るだけでは完了しない。EPGジョブは空きチューナーを必要とし、長時間の視聴・録画で専有されると遅れる。実機で `/api/services`、番組表、サービス別視聴、録画ファイルを順に確認する。

[`config.satellite.example.yml`](config.satellite.example.yml) はBS/CSチャンネルを `disabled: true` にした検証用設定。GRと衛星を同じ `tuners` エントリーへ登録する。衛星実機検証が済むまでチャンネルを有効化しない。BSの既存検証範囲とCSの制約は以下を参照する。

## Docker

[`docker-compose.example.yml`](docker-compose.example.yml) はホストsystemdの `pixd` とコンテナmirakcを結ぶ構成。ホストで `/run/pixd` を作るpixdを先に起動し、同じディレクトリをコンテナへbind mountする。コンテナには USB デバイスを渡さない。`pix-ts` は同じCPUアーキテクチャで Alpine と実行互換のある静的 Linux x86_64 バイナリを `integrations/mirakc/bin/pix-ts` に配置する。リポジトリの `tools/build-portable.sh musl` をmuslビルド環境で実行した場合の出力は `build/musl/pix-ts`。通常のglibcビルドをAlpineへそのままマウントしない。

```sh
cd integrations/mirakc
mkdir -p bin epg
cp /path/to/static-linux-x86_64/pix-ts bin/pix-ts
printf 'PIX_UID=%s\nPIX_GID=%s\nPIX_RUNTIME_GID=%s\n' \
  "$(id -u pixd)" "$(id -g pixd)" "$(getent group pixd | cut -d: -f3)" > .env
docker compose -f docker-compose.example.yml config --quiet
docker compose -f docker-compose.example.yml up -d
```

`epg` の所有者とコンテナユーザーを一致させる。`PIX_UID` はpixd socketの所有者と同じにするか、`PIX_RUNTIME_GID` のグループ権限を付ける。mirakc本体もこのユーザーで起動するため、EPG保存先への書込みを確認する。composeのhealthcheckはmirakc APIの稼働だけを検査し、USB取得成功までは証明しない。composeには `privileged: true` と `/dev/bus/usb` を付けない。コンテナと同時にpixdも起動する別構成では USB ノードの個別パススルー、udev許可、起動順、再接続、PID 1のシグナル伝播、プロセス監視が追加で必要になるため、この例はホストpixdを採用した。k3sで使う場合も同一ノードへの固定配置、USBノード許可、hostPath socketの権限とPod再起動時の接続切断を設計する必要がある。

## 実機なしの再現テスト

```sh
python3 -m unittest discover -s integrations/mirakc/tests -v
ctest --test-dir build/core-integration --output-on-failure
python3 integrations/mirakc/tests/test_mirakc_docker.py
python3 integrations/mirakc/tests/test_mirakc_docker.py --config-sample config.satellite.example.yml
```

最初はmock `pix-ts` による引数、出力バイト一致、188バイトTS、大量出力、バックプレッシャー、pipe切断、SIGTERM/SIGINT、競合と再利用を検査する。CTestの `pix_mirakc_ipc_proof` は現行 `pix-ts` と `pixd_fixture` の実ソケット経路を使い、未起動、競合、STOP/RELEASE、連続受信、pixd再起動後を検査する。このIPC試験の合成USB backendはDT181で、別の `pix_dt295_adapter_lifecycle_proof` がDT295 backendの状態遷移を合成USBで検査する。最後のテストはDocker daemon と `mirakc/mirakc:alpine` が必要で、独立の合成PAT/PMT/NIT/SDTを使って実mirakcの設定読込、HTTPチャンネルストリーム、切断後の解放・再利用、`/api/services` のサービス認識を確認する。合成TSにはEIT/AVはないため、EPG内容、実視聴、録画品質はこのテストでは確認できない。テストコンテナは終了時に削除する。

## 実機入手後の検証順序

下記は**実行手順**であり、今回の実行結果ではない。まずassetsの正当な入力を準備し、機器のserial selectorと地域の物理チャンネルを確認する。`/run/pixd`、systemd、udev、コンテナは上記設定を済ませる。TSは個人の受信環境で扱い、鍵や生データをログへ含めない。

```sh
pixd --list-json
sudo systemctl start pixd
pixctl --instance dt295 --runtime-dir /run/pixd --group status
pix-mirakc-tuner --type GR --channel T27 --duration 15 --instance dt295 --runtime-dir /run/pixd --group > /tmp/dt295-gr.ts 2> /tmp/dt295-gr.log
python3 -c 'import sys; p=open(sys.argv[1],"rb").read(); assert p and len(p)%188==0 and all(p[i]==0x47 for i in range(0,len(p),188)); print(len(p)//188,"packets")' /tmp/dt295-gr.ts
python3 -c 'import sys; p=open(sys.argv[1],"rb").read(); bad=[i//188 for i in range(0,len(p),188) if p[i+3]&0xc0]; print("scrambled packets:",len(bad)); sys.exit(bool(bad))' /tmp/dt295-gr.ts
pixctl --instance dt295 --runtime-dir /run/pixd --group status
pix-mirakc-tuner --type GR --channel T27 --duration 15 --instance dt295 --runtime-dir /run/pixd --group > /tmp/dt295-gr-again.ts 2> /tmp/dt295-gr-again.log
docker compose -f integrations/mirakc/docker-compose.example.yml up -d
curl -fsS http://127.0.0.1:40772/api/tuners
curl -fsS http://127.0.0.1:40772/api/services
curl -fsS --max-time 5 'http://127.0.0.1:40772/api/channels/GR/T27/stream?decode=0' -o /tmp/dt295-mirakc.ts
```

最後の `curl --max-time` は連続ストリームを意図的に切るため timeout exit 28 があり得る。mirakc 側でチューナーがfreeへ戻り、次回ストリームが再取得できることを確認する。別クライアントの同時起動はbusyを返すこと、サービス別ストリーム、EPGのEIT、実際の再生と短時間録画を検証する。BSは正規の衛星bundleとアンテナ給電を確認後 `BS01_0` と既知TSIDの両方を短時間試す。CSは `CS22@29024` など明示TSIDでPAT/PMTとAV出力を個別に検査し、複数ECM制約を成功と見なさない。最後に30分以上の連続受信、正常停止、pixdの通常終了、新しいpixdでのACQUIREを検査する。異常時はUSB resetを自動で繰り返さず、最初のエラー段階を記録する。

## 既知問題と残作業

- [#2](https://github.com/Khronos31/pix-userland/issues/2): 追加音声PIDの消失・再出現境界に部分PES。アダプターでは修正できない。
- [#18](https://github.com/Khronos31/pix-userland/issues/18): HAOSで短時間TSのCC異常。原因帰属は未確定。実機の独立TS検査が必要。
- [#20](https://github.com/Khronos31/pix-userland/issues/20): CS22の複数独立ECM/共有PID計画制限と有料候補のAV未出力。CS対応完了とはしない。
- [#29](https://github.com/Khronos31/pix-userland/issues/29): macOSで30分受信後のUSB close失敗・新規ACQUIRE失敗。Linuxで同じ現象が起こるかは未確認。
- 動的PMTのECM追加・既存PID再割当などは既存ドライバが安全側に停止する。今回の連携はその制約を変更しない。

これらの原因を推測してCドライバやCore APIを変更していない。実機の境界検証と、必要なら最小再現を得てから修正する。
