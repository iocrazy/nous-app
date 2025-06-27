import requests

urls = [
    "https://v3-web.douyinvod.com/c9489cba81ce126f201ada5d934c16c3/685c0a2f/video/tos/cn/tos-cn-ve-15/oQVPTRMyZAjFTsSViEIGlzakBPIE7Qjz7iiAO/?a=6383\u0026ch=10010\u0026cr=3\u0026dr=0\u0026lr=all\u0026cd=0%7C0%7C0%7C3\u0026br=1071\u0026bt=1071\u0026cs=0\u0026ds=3\u0026ft=khyHAB1UiiuGzJrZ~d9C~49Zyo3nOz7nk9SLpMyRP2_4NrE2B22IMEYPkQBWThd.o~\u0026mime_type=video_mp4\u0026qs=0\u0026rc=OWk4OTlnOWQ5aGhnZDk4ZUBpM2ZpeG05cm9zNDMzNGkzM0AxYi9jXjYxXmExLy1gNmEvYSMzNHBlMmQ0MTZhLS1kLWFzcw%3D%3D\u0026btag=80000e00008000\u0026cquery=100o\u0026dy_q=1750858781\u0026feature_id=fea919893f650a8c49286568590446ef\u0026l=20250625213941EA9CD6D0AFF19EBD1CD7",
    "https://v26-web.douyinvod.com/23fd2a754c8d1799650cf7f832a7ce85/685c0a2f/video/tos/cn/tos-cn-ve-15/oQVPTRMyZAjFTsSViEIGlzakBPIE7Qjz7iiAO/?a=6383\u0026ch=10010\u0026cr=3\u0026dr=0\u0026lr=all\u0026cd=0%7C0%7C0%7C3\u0026br=1071\u0026bt=1071\u0026cs=0\u0026ds=3\u0026ft=khyHAB1UiiuGzJrZ~d9C~49Zyo3nOz7nk9SLpMyRP2_4NrE2B22IMEYPkQBWThd.o~\u0026mime_type=video_mp4\u0026qs=0\u0026rc=OWk4OTlnOWQ5aGhnZDk4ZUBpM2ZpeG05cm9zNDMzNGkzM0AxYi9jXjYxXmExLy1gNmEvYSMzNHBlMmQ0MTZhLS1kLWFzcw%3D%3D\u0026btag=80000e00008000\u0026cquery=100o\u0026dy_q=1750858781\u0026feature_id=fea919893f650a8c49286568590446ef\u0026l=20250625213941EA9CD6D0AFF19EBD1CD7",
    "https://www.douyin.com/aweme/v1/play/?video_id=v0d00fg10000d19p4l7og65jhusf7c60\u0026line=0\u0026file_id=2bd596ca0d2e4ed6aa469da6f625ef46\u0026sign=c3749d6849d5d18bdabefbb979626e97\u0026is_play_url=1\u0026source=PackSourceEnum_PUBLISH"
]

for i, url in enumerate(urls, 1):
    response = requests.get(url)
    if response.status_code == 200:
        ext = url.split('.')[-1].split('?')[0]
        filename = f"douyin_image_{i}.{ext}"
        with open(filename, 'wb') as f:
            f.write(response.content)
        print(f"Downloaded {filename} successfully.")
    else:
        print(f"Failed to download image {i}, status code: {response.status_code}")
