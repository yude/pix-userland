# Debian ホストで pixd を常駐させる

PIX-DT295 の USB をホストの `pixd` が所有し、mirakc コンテナは現行 `pix-ts` から共有 Unix socket に接続する構成。`pixd.service` はrootで起動し、ファームウェア埋め込み有効でビルドした `pixd` を使う。専用ユーザー作成やUSB用udev ruleは不要。実機での起動・USB再接続は未検証。

## インストール

リポジトリのルートで実行する。既存の同名サービスがある場合は設定内容を確認してから置き換える。

```sh
sudo install -m 0755 build/cmake/pixd build/cmake/pixctl build/cmake/pix-ts /usr/local/bin/
sudo install -m 0644 packaging/systemd/pixd.service /etc/systemd/system/pixd.service
sudo install -m 0644 packaging/systemd/pixd.default.example /etc/default/pixd
```

次の一覧から実際の `serial:...` または `usb:BUS-PORT` を選び、`/etc/default/pixd` の `PIX_DEVICE` を変更する。USBポートのselectorにはデバイスアドレスではなくポート経路を使う。`PIX_INSTANCE=dt295` はクライアント側とそろえる。

```sh
sudo /usr/local/bin/pixd --list-json
sudoedit /etc/default/pixd
sudo systemd-analyze verify /etc/systemd/system/pixd.service
sudo systemctl daemon-reload
sudo systemctl enable --now pixd.service
sudo systemctl status pixd.service --no-pager
sudo journalctl -u pixd.service -n 50 --no-pager
sudo pixctl --instance dt295 --runtime-dir /run/pixd --group status
```

`pixd` はフォアグラウンドで動作し、systemd の SIGTERM で停止する。`Type=simple` のため `active` は機器準備完了の保証ではない。`pixctl status` と実際の短時間TS取得まで確認する。カードは本体に挿す。PC/SCサーバーは不要。

unit は `User=root`、`Group=video` で起動し、`/run/pixd` を `root:video`、0770 で作る。UIDはrootのままで、既存のvideoグループはIPC共有に使う。一般ユーザーのクライアントには `sudo usermod -aG video USERNAME` でアクセスを与え、再ログイン後に使用する。`ProtectSystem=strict` でも systemd が管理するランタイムディレクトリには書き込める。USBアクセスのため `PrivateDevices=yes` は設定しない。

## mirakc コンテナとの接続

mirakc側には次のbind mountと補助グループを設定する。GIDはホストの `getent group video` で確認する。mirakcは `/run/pixd` 内を変更するためread-only mountにはしない。`pix-ts` とmirakc用アダプターにも `--instance dt295 --runtime-dir /run/pixd --group` を指定する。

```yaml
services:
  mirakc:
    group_add:
      - '${PIX_RUNTIME_GID}'
    volumes:
      - /run/pixd:/run/pixd
```

ホストでpixdを起動し、`pixctl status` を確認してからmirakcを起動する。ホストサービスとCompose間には `depends_on` は使えない。`RuntimeDirectoryPreserve=restart` によりsystemdの再起動時は `/run/pixd` を保持する。明示的なstop後のstartやホスト再起動では作り直されるため、既存コンテナのbind mountが古いディレクトリを参照する場合がある。その際はpixdの復旧後に `docker compose up -d --force-recreate mirakc` でmountを作り直す。

## 復旧と設定変更

異常終了時は5秒間隔で再起動するが、60秒間に3回で停止する。USB状態が壊れた場合に無制限に再取得し続ける構成にはしていない。USB再接続による自動復旧は保証しない。ログとデバイスの権限・selectorを確認し、必要な再接続を行った後で次を実行する。

```sh
sudo systemctl reset-failed pixd.service
sudo systemctl restart pixd.service
sudo pixctl --instance dt295 --runtime-dir /run/pixd --group status
```

`/etc/default/pixd` の変更はサービス再起動で反映される。unitの変更には `systemctl daemon-reload` も必要。

埋め込み無効ビルドではファームウェアを `/usr/local/share/pix-userland/dt295.bin` などへ配置し、`sudo systemctl edit pixd.service` で次のoverrideを追加する。`ProtectHome=yes` のためホームディレクトリ内のファイルは使わない。

```ini
[Service]
ExecStart=
ExecStart=/usr/local/bin/pixd --device ${PIX_DEVICE} --instance ${PIX_INSTANCE} --runtime-dir /run/pixd --group --firmware /usr/local/share/pix-userland/dt295.bin
```
