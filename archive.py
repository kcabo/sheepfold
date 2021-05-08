import asyncio
import re
import os

from pyppeteer import launch

interval_time = 15000
BASE_MERY_URL = 'https://mery.jp'
DEBUG = False
launch_args = ['--start-maximized']
pattern = r'作成：(.{10})'
file_name_conv_table = str.maketrans(r'\/:*?"<>| ', r'＼／：＊？”＜＞｜_')


class Writer:

    def __init__(self, box_id):
        self.box_id = box_id
        self.box_url = f'{BASE_MERY_URL}/boxes/{box_id}'


    async def set_writer_name(self):
        browser = await launch(headless=False, args=launch_args) if DEBUG else await launch()
        page = await browser.newPage()

        await page.goto(self.box_url, {'waitUntil': 'domcontentloaded'})

        # ライター名を取得
        try:
            name_elements = await page.xpath('//*[@id="topBar"]/ul/li[2]/a/span')
            name_with_space = await page.evaluate('elm => elm.textContent', name_elements[0])
            self.name = name_with_space.strip()

        except IndexError as e:
            print('>> User IDを入力していませんか？mery.jp/boxes/に続く数字を入れてください')
            raise e

        finally:
            await browser.close()


    async def fetch_article_counts(self):
        browser = await launch(headless=False, args=launch_args) if DEBUG else await launch()
        page = await browser.newPage()

        await page.goto(self.box_url, {'waitUntil': 'domcontentloaded'})

        # 記事数を取得
        article_count_elements = await page.xpath('//*[@id="column_content"]/section[1]/div[2]')
        article_count_with_units = await page.evaluate('elm => elm.textContent', article_count_elements[0])
        self.article_count = int(article_count_with_units.strip()[:-1])

        await browser.close()


    def arrange_pages(self):
        # 全体の記事数から記事一覧表のページ数を算出
        max_articles_showed_in_single_table = 20
        pages_count = self.article_count // max_articles_showed_in_single_table + 1

        # 各ページのインスタンスを作成
        pages = [
            ListPage(self.box_id, order)
            for order in range(1, pages_count + 1)
        ]
        return pages


    def generate_folder(self):
        path = self.name
        os.makedirs(path, exist_ok=True)

   
# 記事一覧ページ 1Boxにつき複数ある
class ListPage:
    def __init__(self, box_id, order):
        self.order = order
        self.url = f'{BASE_MERY_URL}/boxes/{box_id}?page={order}'


class Article:
    def __init__(self, writer_name, index, url):
        self.writer_name = writer_name
        self.index = index
        self.url = url
    
    def __str__(self):
        return f'{self.index:_>4} {self.url}'


def input_target_box_id():
    print('アーカイブ対象の「つくった記事」BOX IDを入力してください')
    box_id_raw = input('https://mery.jp/boxes/')
    try:
        box_id = int(box_id_raw)
    except ValueError as e:
        print('整数を入力してください')
        return
    else:
        return box_id


def save_urls(writer_name, urls):
    file_path = f'{writer_name}/urls.csv'
    content = '\n'.join(urls)

    with open(file_path, mode='w') as f:
        f.write(content)


async def fetch_article_urls_from_table(list_page):
    print(f'{list_page.order}ページ目の全記事のURLを取得しています...')
    browser = await launch(headless=False, args=launch_args) if DEBUG else await launch()
    page = await browser.newPage()

    # 記事一覧表ページに遷移
    await page.goto(list_page.url, {'waitUntil': 'domcontentloaded'})

    # aタグからhref属性を取得
    link_tags = await page.querySelectorAll('#column_content .box-article-list .article_list_text a')
    article_ids = [
        await page.evaluate('elm => elm.getAttribute("href")', link_tag)
        for link_tag in link_tags
    ]

    await page.waitFor(interval_time) # 負荷軽減のため待機
    await browser.close()
    print(f'{list_page.order}ページ目の全記事のURLの取得完了')
    return article_ids


async def limited_parallel_call(func, args, limit=5):
    """セマフォ変数を利用し、並列処理の同時実行数を制限する"""
    sem = asyncio.Semaphore(limit)

    async def call(arg):
        async with sem:
            return await func(arg)

    return await asyncio.gather(*[call(x) for x in args])


async def archive_page(article):
    print(article, end=' ', flush=True)
    browser = await launch(headless=False, args=launch_args) if DEBUG else await launch()
    page = await browser.newPage()

    await page.goto(article.url, {'waitUntil': 'domcontentloaded', 'timeout': 200000})
    written_date = await find_date(page)  # 記事作成日を取得
    title = await find_title(page)  # 記事タイトルを取得

    # 記事以外の要素を全て消す
    await page.evaluate("""() => {
        $("#wrapper").siblings().css("display", "none");
        $("#column_content").siblings().css("display", "none");
        $("#article").siblings().css("display", "none");
        $(".articleArea").nextAll().css("display", "none");
    }""")

    # 遅延読み込み対策に下まで読み込む
    await page.evaluate(f"""() => {{
        $('html, body').animate({{
        scrollTop: $(document).height()
        }},{interval_time});
    }}""")
    await page.waitFor(interval_time)

    file_name_unsafe = f'{written_date}_{title}.pdf'
    file_name_safe = file_name_unsafe.translate(file_name_conv_table)  # ファイル名に使えない文字列を置換
    file_path = f'{article.writer_name}/{file_name_safe}'

    # pdf出力
    await page.emulateMedia('screen')
    if DEBUG == False:
        await page.pdf({
            'path': file_path,
            'scale': 1.3,
            'printBackground': True,
            'format': 'A4',
            'margin': {'left': '2cm'}
        })

    print('-> ', file_path)

    await browser.close()


async def find_date(page):
    # 記事の作成日をページから取得する
    article_info = await page.xpath('//*[@id="article"]/div[1]/p')
    article_info_text = await page.evaluate('elm => elm.textContent',article_info[0])
    result = re.search(pattern, article_info_text)
    written_date = result.group(1)
    return written_date


async def find_title(page):
    # 記事のタイトルをページから取得する
    h1 = await page.querySelector('h1')
    title_raw = await page.evaluate('elm => elm.textContent', h1)
    title = title_raw.strip()
    return title


def main():
    loop = asyncio.get_event_loop()

    box_id = input_target_box_id()
    if not box_id: return # box_idが整数でないとき抜ける

    writer = Writer(box_id)
    loop.run_until_complete(writer.set_writer_name())
    loop.run_until_complete(writer.fetch_article_counts())

    print(f'対象:{writer.name}さん 全{writer.article_count}記事のアーカイブを開始')

    # 記事一覧ページの全ページ
    list_pages = writer.arrange_pages()

    # 記事一覧表から記事のIDを取得
    article_ids_2d = loop.run_until_complete(
        limited_parallel_call(fetch_article_urls_from_table, list_pages, limit=3)
    )
    article_ids = sum(article_ids_2d, []) # 二次元配列を平坦化

    # CSVファイルにURLの形で書き込み
    urls = [BASE_MERY_URL + article_id for article_id in article_ids]
    writer.generate_folder()
    save_urls(writer.name, urls)

    articles = [Article(writer.name, i, url) for i, url in enumerate(urls, 1)]

    # アーカイブ開始
    print(f'{len(urls)}ページのアーカイブを開始')
    loop.run_until_complete(
        limited_parallel_call(archive_page, articles, limit=1)
    )
    print('COMPLETE!')


if __name__ == '__main__':
    main()