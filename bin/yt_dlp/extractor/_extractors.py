# flake8: noqa: F401
from .bilibili import (
    BiliBiliIE,
    BiliBiliBangumiIE,
    BiliBiliBangumiMediaIE,
    BiliBiliBangumiSeasonIE,
    BilibiliCheeseIE,
    BilibiliCheeseSeasonIE,
    BilibiliSpaceVideoIE,
    BilibiliSpaceAudioIE,
    BilibiliCollectionListIE,
    BilibiliSeriesListIE,
    BilibiliFavoritesListIE,
    BilibiliWatchlaterIE,
    BilibiliPlaylistIE,
    BilibiliCategoryIE,
    BiliBiliSearchIE,
    BilibiliAudioIE,
    BilibiliAudioAlbumIE,
    BiliBiliPlayerIE,
    BiliBiliDynamicIE,
    BiliIntlIE,
    BiliIntlSeriesIE,
    BiliLiveIE,
)

# 只保留bilibili平台，GenericIE用BiliBiliIE兜底
GenericIE = BiliBiliIE
