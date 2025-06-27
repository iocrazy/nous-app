import asyncio
from app.services.douyin_analysis import DouyinAnalysis
from app.core.utils import Utils
from tests.douyin_analysis_test import DouyinService
import datetime
from loguru import logger

async def main():

    try:
        # valid_url = Utils.extract_valid_url("https://v.douyin.com/L4FJNR3/")[0]

        # 获取视频详情
        aweme_detail = await DouyinService.fetch_one_video("https://www.douyin.com/note/7517414436882337083")
        valid_url="https://v.douyin.com/pH5vzARvTSY"

        # 提取视频aweme_id
        aweme_type=aweme_detail.get("aweme_type")
        aweme_id = aweme_detail.get("aweme_id")
        print("aweme_id:", aweme_id,"media_type:",aweme_type)


        if aweme_type==68:  #图文

            images= aweme_detail.get("images", [])
            image_download_urls = []
            video_download_urls = []
            if images:
                for item in images:
                    if not item.get("video", {}):
                        image_urls = item.get("download_url_list", [])
                        image_download_urls.append(image_urls)
                        logger.info(f"image_url_list: {image_download_urls}")
                    if item.get("video", {}):
                        video_urls = item.get("video", {}).get("play_addr", {}).get("url_list", [])
                        video_download_urls.append(video_urls)
                        logger.info(f"video_url_list: {video_download_urls}")

            video_data = {
                "aweme_id": aweme_id,
                "author": aweme_detail.get("author", {}).get("nickname"),
                "video_original_url": valid_url,
                "video_title": aweme_detail.get("desc", "undefined"),
                "video_digg_count": aweme_detail.get("statistics", {}).get("digg_count"),
                "video_comment_count": aweme_detail.get("statistics", {}).get("comment_count"),
                "video_share_count": aweme_detail.get("statistics", {}).get("share_count"),
                "video_collect_count": aweme_detail.get("statistics", {}).get("collect_count"),
                "media_type": str(aweme_detail.get("aweme_type")),
                "video_hashtag_name": aweme_detail.get("caption", Utils.concat_hashtag_name(aweme_detail)),
                "video_created_time": datetime.datetime.fromtimestamp(aweme_detail.get("create_time")),
                "image_download_urls": image_download_urls,
                "video_download_urls": video_download_urls,

                "music_download_urls": aweme_detail.get("music", {}).get("play_url", {}).get("url_list",None),
                "music_name": f"{aweme_id}_{aweme_detail.get("music", {}).get("author","undefined")}-{aweme_detail.get("music", {}).get("title","undefined")}"

            }


        elif aweme_type== 2: #图片 合集
            video_data = {
                "aweme_id": aweme_id,
            }


        elif aweme_type == 0: #视频

            # 提取音乐
            music_from = aweme_detail.get("music", {}).get("title")
            music_author = aweme_detail.get("music", {}).get("matched_pgc_sound", {}).get("author")
            music_title = aweme_detail.get("music", {}).get("matched_pgc_sound", {}).get("title")

            music_name = f"{aweme_id}_{music_author}-{music_title}" if music_author and music_title else f"{aweme_id}:{music_from}"

            video_data = {
                "aweme_id": aweme_id,
                "author": aweme_detail.get("author", {}).get("nickname"),
                "video_original_url": valid_url,
                "video_title": aweme_detail.get("desc", "undefined"),
                "video_digg_count": aweme_detail.get("statistics", {}).get("digg_count"),
                "video_comment_count": aweme_detail.get("statistics", {}).get("comment_count"),
                "video_share_count": aweme_detail.get("statistics", {}).get("share_count"),
                "video_collect_count": aweme_detail.get("statistics", {}).get("collect_count"),
                "video_hashtag_name": aweme_detail.get("caption", Utils.concat_hashtag_name(aweme_detail)),
                "media_type": str(aweme_detail.get("aweme_type")),
                "video_created_time": datetime.datetime.fromtimestamp(aweme_detail.get("create_time")),

                "video_datasize": Utils.format_file_size(aweme_detail.get("video", {}).get("bit_rate", [{}])[0].get("play_addr", {}).get("data_size", None)),
                "video_duration": Utils.format_duration(aweme_detail.get("video", {}).get("duration")),
                "Resolution": f"{aweme_detail.get('video', {}).get('width')}:{aweme_detail.get('video', {}).get('height')}",
                "video_download_urls": aweme_detail.get("video", {}).get("play_addr", {}).get("url_list", None),


                "music_download_urls": aweme_detail.get("music", {}).get("play_url", {}).get("url_list", None),
                "music_name": music_name
            }
        else:
            print("不支持的aweme_type:", aweme_type)




    except Exception as e:
        print(f"Error fetching video: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
