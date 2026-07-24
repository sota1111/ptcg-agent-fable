# fable 上位帯リプレイ手順単位敗着解析 (SOT-1894)

生成: `python analysis/kaggle_replays.py && python analysis/analyze_replays.py`

## データ可用性の記録 (受け入れ条件1)

- 確認日時: 2026-07-24T01:57:17Z / probe episode 86732867
- 内部RPC `GetEpisodeReplay`: **HTTP 404** (未復旧のまま)
- 公開エンドポイント `kaggleusercontent.com/episodes/<id>.json`: **HTTP 200** — kaggle-environments形式のフルリプレイ (steps/board/select/action) が取得可能
- 判定: **手順単位データは公開エンドポイント経由で入手可** — 本Issueのデータ待ちゲートは開通。

## 対象データ

- 解析した敗北エピソード: **121** 件（うち相手が同格以上 103 件）

## 敗着の一次分類 (エンジンRESULT理由)

| 理由 | 件数 |
| --- | --- |
| board_wipe | 107 |
| reason_None | 13 |
| deck_out | 1 |

## 手順単位の敗着シグナル (全敗北)

- 2枚以上プライズを一度に献上 (multi-prize KO被弾) があった敗北: **23%**
- リードを持ちながら逆転負け (late reversal): **63%**
- 一度もリードできず敗北 (never led): **37%**
- 決定的ブレーク時点の平均ベンチ数: **0.46**
- ブレーク周辺で攻撃可能なのにターン終了を選んだ敗北: **5** 件

## 盤面全滅 (board_wipe) のメカニズム分解

wipe = active KO時にベンチ0で即敗北。その時フェイブルに「選択の余地」があったか:

- ベンチ0でMAINにPLAY(たね)が提示されていたのにATTACK/ENDで見送った敗北: **10** 件 / 見送り総数 22 回（= 決定点で防げた可能性がある wipe）
- wipe 107 件のうち、最終決定時に手札にたねが1枚もない: **92%**（= 決定点では防げない資源枯渇）
- たね0のまま進化カードだけ握って死んでいる (dead evolutions): **56%**
- 双方プライズ0のまま終了 (セットアップ負け): **36%**
- プライズレースはリードしたまま wipe 負け: **36%**
- 相手は最終盤面でベンチ3枚以上: **72%**

ブレーク周辺 (決定的ターン±1) の選択コンテキスト分布:

| context | 回数 |
| --- | --- |
| MAIN | 449 |
| TO_HAND | 82 |
| TO_ACTIVE | 34 |
| DISCARD_ENERGY | 28 |
| ATTACH_TO | 21 |
| ATTACH_FROM | 21 |
| TO_DECK | 2 |

## エピソード別の決定的ブレーク

decisive turn = プライズレースで最後に同点以上だったターン（以降は差が戻らない）。

| episode | 相手 (rating) | 理由 | 総ターン | 決定タ-ン | max lead | multi-prize被弾 | ベンチ数@break |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 86732867 | Dieter (1100) | reason_None | 7 | 5 | 1 | T5x3 | 2 |
| 87057986 | Dominic Peel (1035) | board_wipe | 3 | 3 | 0 | - | 0 |
| 87393888 | Marshall Maximizer (934) | board_wipe | 8 | 4 | 0 | - | 1 |
| 87335968 | Ingenio (839) | board_wipe | 5 | 5 | 0 | - | 0 |
| 87310646 | Edith Yong (836) | board_wipe | 12 | 12 | 1 | - | 0 |
| 87614928 | tktkyamyam (820) | board_wipe | 10 | 10 | 2 | - | 0 |
| 86717639 | MooDerEchte (806) | board_wipe | 11 | 11 | 2 | - | 0 |
| 87282098 | Phil_Hellmuth (806) | reason_None | 11 | 7 | 6 | T7x3 | 1 |
| 87009796 | Yunioshi (802) | board_wipe | 8 | 8 | 0 | - | 0 |
| 86718174 | MCH (783) | board_wipe | 4 | 4 | 6 | - | 0 |
| 87620636 | Tanupro (760) | board_wipe | 7 | 7 | 1 | - | 0 |
| 87630699 | Saulo Quiñones Góngora (745) | board_wipe | 8 | 6 | 0 | - | 1 |
| 86847319 | ハットにゃん (743) | board_wipe | 6 | 6 | 2 | - | 0 |
| 87310103 | neibyr (737) | board_wipe | 4 | 4 | 0 | - | 0 |
| 87615485 | Robson (735) | reason_None | 23 | 19 | 6 | T13x3 | 2 |
| 87477569 | Alfonso Sánchez (731) | board_wipe | 8 | 4 | 0 | - | 1 |
| 87399702 | Nishant Dahal (728) | board_wipe | 10 | 10 | 4 | - | 0 |
| 87476501 | TA (727) | board_wipe | 11 | 11 | 4 | - | 0 |
| 86722480 | FinlaMor (715) | board_wipe | 2 | 2 | 0 | - | 0 |
| 86728095 | shu-piplup (711) | board_wipe | 10 | 10 | 1 | - | 0 |
| 87621493 | Haramball forever! (708) | board_wipe | 6 | 4 | 0 | - | 1 |
| 86723017 | dafujii (696) | board_wipe | 12 | 12 | 2 | - | 0 |
| 86732778 | moohsin (694) | reason_None | 12 | 12 | 1 | - | 2 |
| 87631227 | lxh unbound (682) | board_wipe | 8 | 8 | 5 | - | 0 |
| 86730823 | ima_AI123 (681) | board_wipe | 8 | 8 | 0 | - | 0 |
| 87269261 | Shinichiro Yoshida (680) | board_wipe | 3 | 3 | 0 | - | 0 |
| 86731494 | ryoya (677) | board_wipe | 4 | 4 | 0 | - | 0 |
| 87621018 | Rajan Nagarajan (676) | board_wipe | 8 | 8 | 2 | - | 0 |
| 87490036 | Mikhail Breikin (674) | board_wipe | 7 | 7 | 6 | - | 0 |
| 86729423 | Takamasa Muto (670) | board_wipe | 6 | 6 | 2 | - | 0 |
| 87630159 | Ni123456& (668) | board_wipe | 12 | 10 | 3 | T9x3 | 1 |
| 86732879 | Kimiaki Nakamura (667) | board_wipe | 12 | 12 | 6 | - | 0 |
| 87626388 | 失忆的海_ (664) | board_wipe | 4 | 4 | 0 | - | 0 |
| 87341004 | MJVinay (662) | reason_None | 10 | 10 | 3 | T8x3 | 1 |
| 87273371 | Latte (656) | board_wipe | 12 | 8 | 0 | - | 1 |
| 87624802 | U023 (654) | board_wipe | 16 | 16 | 3 | - | 0 |
| 86725257 | G_nuoh (653) | board_wipe | 3 | 3 | 0 | - | 0 |
| 87625901 | Yunus Emre Sarıduman (652) | board_wipe | 8 | 8 | 2 | - | 0 |
| 87631771 | datawizardd (651) | board_wipe | 13 | 7 | 6 | T7x3 | 2 |
| 87488430 | Aiagate (650) | board_wipe | 24 | 22 | 1 | - | 1 |
| 87632850 | Keisuke_kaggle (645) | board_wipe | 5 | 3 | 0 | - | 1 |
| 87310533 | hikahika (642) | board_wipe | 3 | 3 | 6 | - | 0 |
| 87324511 | matsubarajotaro (641) | board_wipe | 10 | 4 | 0 | T7x3 | 1 |
| 87283100 | Sahoooooo! (641) | board_wipe | 3 | 3 | 6 | - | 0 |
| 87313255 | Pokkén (639) | board_wipe | 6 | 4 | 0 | - | 1 |
| 87648680 | Felis (639) | board_wipe | 13 | 9 | 6 | T9x3 | 1 |
| 86734114 | Arkat Khassanov (637) | board_wipe | 4 | 2 | 0 | - | 1 |
| 87619365 | Myagi (637) | board_wipe | 10 | 10 | 3 | T9x3 | 0 |
| 86719796 | mewworldorder (636) | reason_None | 16 | 16 | 5 | T13x3 | 2 |
| 87358116 | kinakomochi (634) | board_wipe | 9 | 9 | 0 | - | 0 |
| 86755850 | Filip Strzałka (626) | board_wipe | 8 | 8 | 1 | - | 0 |
| 87332652 | NaviCE (624) | board_wipe | 8 | 8 | 4 | - | 0 |
| 87485720 | Dylan Dove (619) | board_wipe | 11 | 11 | 0 | - | 0 |
| 87218642 | user_san (619) | board_wipe | 9 | 7 | 2 | T7x3 | 1 |
| 87485172 | Stef Limited (617) | reason_None | 13 | 13 | 2 | T7x3 | 1 |
| 87320812 | TK0227 (617) | board_wipe | 2 | 2 | 0 | - | 0 |
| 86733449 | Klee319 (614) | board_wipe | 4 | 4 | 0 | - | 0 |
| 87478115 | TrustHub hiroingk (614) | board_wipe | 7 | 7 | 6 | - | 0 |
| 87477035 | nugui-nugui (611) | board_wipe | 6 | 6 | 1 | - | 0 |
| 86734740 | YT (610) | board_wipe | 3 | 3 | 6 | - | 0 |
| 86735407 | peacemonkey (609) | deck_out | 57 | 57 | 4 | - | 3 |
| 87313805 | iwtn (606) | board_wipe | 2 | 2 | 0 | - | 0 |
| 87150560 | YounsungLEE (601) | board_wipe | 5 | 5 | 0 | - | 0 |
| 87622588 | MoMy (601) | board_wipe | 6 | 6 | 0 | - | 0 |
| 87638713 | kurushun (601) | reason_None | 16 | 14 | 1 | T15x3 | 2 |
| 87309449 | G_nuoh (600) | board_wipe | 5 | 3 | 0 | - | 1 |
| 86762772 | Dominic Peel (600) | board_wipe | 10 | 10 | 4 | - | 0 |
| 87314261 | Jason-Oh (600) | board_wipe | 3 | 3 | 6 | - | 0 |
| 87623156 | YATAShotaro (588) | board_wipe | 4 | 4 | 0 | - | 0 |
| 87481909 | hondana29 (587) | reason_None | 16 | 14 | 3 | T14x3 | 2 |
| 86725692 | tsuzuk1 (585) | board_wipe | 6 | 4 | 0 | - | 1 |
| 86935679 | Donate to Venezuela (583) | board_wipe | 15 | 15 | 4 | T11x3 | 0 |
| 86794667 | hosoka-r (583) | board_wipe | 6 | 6 | 2 | - | 0 |
| 87319201 | Duru Dökmen (580) | board_wipe | 14 | 14 | 0 | - | 0 |
| 87616047 | nakusnakus (578) | reason_None | 8 | 6 | 1 | T7x3 | 2 |
| 87480283 | Thái Văn Tài (578) | board_wipe | 6 | 6 | 0 | - | 0 |
| 87318670 | cannotflypig (575) | board_wipe | 12 | 12 | 1 | - | 0 |
| 87632305 | mk (574) | board_wipe | 21 | 21 | 3 | T17x3 | 0 |
| 87308395 | MartinZiserman (572) | board_wipe | 11 | 3 | 6 | T7x3 | 1 |
| 86877552 | Spintronic (572) | reason_None | 9 | 3 | 6 | T7x3 | 3 |
| 86828047 | Hiro Nomo (571) | board_wipe | 6 | 6 | 1 | - | 0 |
| 86982199 | The Unovans (570) | board_wipe | 10 | 8 | 1 | T9x3 | 1 |
| 87661357 | 福島広暉 (570) | board_wipe | 10 | 10 | 5 | - | 0 |
| 86775948 | yankang_XZK (567) | board_wipe | 3 | 3 | 0 | - | 0 |
| 86736159 | Prify Philip (563) | board_wipe | 3 | 3 | 0 | - | 0 |
| 87319774 | Hikaru Yamamoto (563) | board_wipe | 10 | 10 | 3 | - | 0 |
| 86742725 | Heros (554) | reason_None | 9 | 5 | 6 | T5x3 | 2 |
| 87418411 | teamAlone (552) | board_wipe | 9 | 3 | 0 | T7x3 | 2 |
| 86721412 | Pepijn Langeraert (551) | board_wipe | 3 | 3 | 0 | - | 0 |
| 86730113 | Insper TCG (548) | board_wipe | 6 | 6 | 1 | - | 0 |
| 87315967 | hieda kazvki (548) | board_wipe | 10 | 10 | 0 | - | 0 |
| 87311071 | William Catt (542) | reason_None | 7 | 3 | 0 | T5x3 | 2 |
| 87616606 | Thazzeus (542) | board_wipe | 12 | 10 | 0 | T11x3 | 1 |
| 86724621 | arm10n (532) | board_wipe | 6 | 6 | 2 | - | 0 |
| 87489494 | bigdan7 (528) | board_wipe | 7 | 7 | 6 | - | 0 |
| 86971602 | nekomanma (526) | board_wipe | 9 | 9 | 0 | - | 0 |
| 86815053 | Ingyun Ahn (524) | board_wipe | 3 | 3 | 6 | - | 0 |
| 87321873 | 齋藤壮汰 (524) | board_wipe | 8 | 8 | 3 | - | 0 |
| 87314343 | チャーハン (520) | board_wipe | 21 | 19 | 3 | T17x3 | 1 |
| 87316526 | Hiroto Tarora (519) | board_wipe | 19 | 19 | 6 | - | 0 |
| 86904478 | Hassan Tavakoli (518) | board_wipe | 6 | 6 | 1 | - | 0 |
| 87325043 | MatsuyamaKAiho (494) | board_wipe | 9 | 3 | 6 | T7x3 | 1 |
| 87486262 | Francisco Stahl (485) | board_wipe | 10 | 10 | 2 | - | 0 |
| 87352496 | zhang xxxy (482) | board_wipe | 6 | 6 | 1 | - | 0 |
| 87113559 | JIBINA EA (469) | board_wipe | 7 | 7 | 2 | - | 0 |
| 87306276 | cha7ura (466) | board_wipe | 8 | 8 | 1 | - | 0 |
| 87478661 | Sai Syam (464) | board_wipe | 8 | 8 | 1 | - | 0 |
| 87306839 | Szymon Kłapiński (437) | board_wipe | 15 | 7 | 0 | T7x3 | 2 |
| 87313355 | af21081 (431) | board_wipe | 5 | 5 | 0 | - | 0 |
| 87307388 | SPARSH PATEL (430) | board_wipe | 21 | 21 | 2 | - | 0 |
| 87313916 | kawaiy (414) | board_wipe | 17 | 17 | 1 | - | 0 |
| 87482974 | Jamie Dixon (399) | board_wipe | 18 | 18 | 3 | - | 0 |
| 87316159 | Matcha2Greentea (359) | board_wipe | 7 | 7 | 0 | - | 0 |
| 87307943 | Benedek Brandschott (345) | board_wipe | 6 | 6 | 0 | - | 0 |
| 87361555 | Lizzi Ti (334) | board_wipe | 11 | 11 | 6 | - | 0 |
| 87315031 | Richard Tang (333) | board_wipe | 5 | 5 | 6 | - | 0 |
| 87311171 | Tuna Xiao (329) | board_wipe | 15 | 9 | 0 | - | 1 |
| 87314468 | JunWoo “Junu1229” Kim (307) | board_wipe | 27 | 21 | 2 | T22x3 | 2 |
| 87342961 | KENMANGG (300) | board_wipe | 7 | 7 | 6 | - | 0 |
| 87362039 | MrSamu18 (284) | board_wipe | 13 | 13 | 1 | - | 0 |
| 87309015 | Choco (246) | board_wipe | 6 | 6 | 0 | - | 0 |

prior設計仮説と検証経路: `docs/replay_prior_hypotheses.md` を参照。
