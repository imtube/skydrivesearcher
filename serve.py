#coding:utf-8

import lucene
import tornado
import tornado.web
import tornado.ioloop
import logging
import string
import json
from pymongo import MongoClient
from time import localtime, strftime, time
from lucene import *
from settings import *
from tools import gen_logger, pagination
from functools import wraps
from IPython import embed

testindexdir = SimpleFSDirectory(File('testindex'))
testsearcher = IndexSearcher(testindexdir)
source_count = None
logger = gen_logger(__file__, 'w')

def traffic_counter(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        db.monitor.update({}, {'$inc':{'traffic':1}})
        return func(*args, **kwargs)
    return wrapper

def rebuild_indexing():
    '''
    :desc:重构索引
    '''
    logger.info('重构索引...')
    start_time = time()
    items = db.source.find()
    global source_count
    source_count = db.source.count()
    logger.info('收录数据 {} 条'.format(source_count))
    writer = IndexWriter(INDEXDIR, ANALYZER, True, IndexWriter.MaxFieldLength.UNLIMITED)
    for item in items:
        doc = Document()
        doc.add(Field('title', item['title'], Field.Store.YES, Field.Index.ANALYZED))
        doc.add(Field('url', item['url'], Field.Store.YES, Field.Index.NOT_ANALYZED))
        doc.add(Field('time', str(item['time']), Field.Store.YES, Field.Index.NOT_ANALYZED))
        writer.addDocument(doc)

    writer.close()
    cost_time = '%.3f s' % (time() - start_time)
    logger.info('重构索引完毕，耗时 {}'.format(cost_time,))

class IndexHandler(tornado.web.RequestHandler):
    @traffic_counter
    def get(self):
        kwargs = dict(
            source_count=source_count,
        )
        self.render('index.html', **kwargs)

class QueryHandler(tornado.web.RequestHandler):
    @traffic_counter
    @tornado.web.asynchronous
    def get(self):
        query_string = self.get_argument('query_string', '').strip()
        page = int(self.get_argument('page', 1))
        if query_string == '':
            self.send_error(400)
        else:
            query = QueryParser(Version.LUCENE_30, 'title', ANALYZER).parse(query_string)
            scorer = QueryScorer(query, 'title')
            highlighter = Highlighter(FORMATTER, scorer)
            highlighter.setTextFragmenter(SimpleSpanFragmenter(scorer))
            start_time = time()
            total_hits = SEARCHER.search(query, RESULT_MAX_NUM)
            cost_time = '%.3f ms' % ((time() - start_time) * 1000,)
            items = []
            for hit in total_hits.scoreDocs:
                doc= SEARCHER.doc(hit.doc)
                title = doc.get('title')
                stream = TokenSources.getAnyTokenStream(SEARCHER.getIndexReader(), hit.doc, 'title', doc, ANALYZER)
                title = highlighter.getBestFragment(stream, title)
                url = doc.get('url')
                ctime = int(doc.get('time'))
                item = dict(
                    title=title,
                    url=url,
                    time=strftime('%Y-%m-%d %H:%M:%S', localtime(ctime))
                )
                items.append(item)

            # 对搜索结果分页
            paging = pagination(items, page, RESULT_PAGE_SIZE)

            kwargs = dict(
                cost_time=cost_time,
                paging=paging,
            )

            self.write(json.dumps(kwargs))
            self.finish()

class OverlookHandler(tornado.web.RequestHandler):
    def get(self):
        self.render('dashboard.html')

class OverlookChartHandler(tornado.web.RequestHandler):
    def get(self):
        chartname = self.get_argument('chartname', None)
        log = {
            'user': list(db.user_log.find()),
            'source': list(db.source_log.find()),
            'traffic': list(db.traffic_log.find()),
        }.get(chartname, None)
        if log is None: self.send_error(400)

        def gen_time(ctime):
            return strftime('%H:%M', localtime(ctime))

        if len(log) <= LOG_LIMIT:
            ctime_list = [gen_time(x['ctime']) for x in log]
            count_list = [x['count'] for x in log]
        else:
            log = log[len(log) - LOG_LIMIT:]
            ctime_list = [gen_time(x['ctime']) for x in log]
            count_list = [x['count'] for x in log]

        data = dict(
            ctime_list=ctime_list,
            count_list=count_list,
        )

        self.write(json.dumps(data))

class OverlookCSVHandler(tornado.web.RequestHandler):
    @tornado.web.asynchronous
    def get(self):
        csvname = self.get_argument('csvname', None)
        csvinfo = {
            'user':[db.user_log, 'user.log.{}.csv'],
            'source':[db.source_log, 'source.log.{}.csv'],
            'traffic':[db.traffic_log, 'traffic.log.{}.csv'],
        }.get(csvname, None)
        if csvinfo is None: self.send_error(400)

        self.set_header('Content-Type', 'application/octet-stream')
        self.set_header('Content-Disposition', 'attachment;filename=' + csvinfo[1].format(int(time())))

        def gen_line(item):
            _ctime = strftime('%Y-%m-%d %H:%M', localtime(item['ctime']))
            count = item['count']
            return '{ctime},{count}\n'.format(ctime=_ctime, count=count)

        def yield_line(collection):
            for item in collection.find():
                yield gen_line(item)

        self.write('ctime,count\n')
        for line in yield_line(csvinfo[0]):
            self.write(line)

        self.finish()

class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        return self.render('index.html')

class IndexInfoHandler(tornado.web.RequestHandler):
    @tornado.web.asynchronous
    def get(self):
        source_count = db.source.count()
        data = dict(
            source_count=source_count,
        )
        self.write(json.dumps(data))
        self.finish()

class TestChineseHandler(tornado.web.RequestHandler):
    def post(self):
        query_string = self.get_argument('query_string')
        query = QueryParser(Version.LUCENE_30, 'content', ANALYZER).parse(query_string)
        scorer = QueryScorer(query, 'content')
        highlighter = Highlighter(FORMATTER, scorer)
        highlighter.setTextFragmenter(SimpleSpanFragmenter(scorer))
        start_time = time()
        total_hits = testsearcher.search(query, RESULT_MAX_NUM)
        items = []

        for hit in total_hits.scoreDocs:
            doc = testsearcher.doc(hit.doc)
            content = doc.get('content')
            stream = TokenSources.getAnyTokenStream(testsearcher.getIndexReader(), hit.doc, 'content', doc, ANALYZER)
            content = highlighter.getBestFragment(stream, content)
            items.append(content)

        self.render('testchinese.html', content=TEST_CHINESE_CONTENT, items=items)

    def get(self):
        self.render('testchinese.html', content=TEST_CHINESE_CONTENT, items=None)

settings = dict(
    debug=True,
    template_path='template',
    static_path='static',
)

application = tornado.web.Application([
    (r'/', IndexHandler),
    (r'/testchinese', TestChineseHandler),
    (r'/info', IndexInfoHandler),
    (r'/query', QueryHandler),
    (r'/admin', OverlookHandler),
    (r'/admin/overlook', OverlookHandler),
    (r'/admin/overlook/chart', OverlookChartHandler),
    (r'/admin/overlook/csv', OverlookCSVHandler),
], **settings)

if __name__ == '__main__':
    # rebuild_indexing()
    application.listen(8888)
    tornado.ioloop.IOLoop.instance().start()
