import MySQLdb
import datetime
import shelve


class doi_finder():
    def __init__(self, lang='enwiki'):
        #@TODO make this languag agnostic
        host = lang+'.labsdb'
        db = lang+'_p'
        self.conn = MySQLdb.connect(host=host, db=db, port=3306, read_default_file='~/replica.my.cnf')
        self.cursor = self.conn.cursor()        
        #shelf is keyed by doi, and values is a list of pages they appear on
        self.shelf = shelve.open('doi_detector_shelf', writeback=False)
        self.check_time = None

    def get_doi_list(self):
        qstring = u'''select page_title, el_to from externallinks left join page on page_id = el_from where page_namespace = 0 and el_index like 'http://org.doi.dx%' '''
        if self.check_time:
            print self.check_time
            qstring += u'''and page_touched > '''
            qstring += self.check_time

        qstring += u''';'''
        uqstring = qstring.encode('utf-8')
            
        self.cursor.execute(uqstring)
        return self.cursor.fetchall()

    def find_new_doi_article_pairs(self):
        curr = self.get_doi_list()
        new_additions = list()
        for title, doi_str in curr:
            doi = doi_str.split('http://dx.doi.org/')[1]
            if doi not in self.shelf.keys():
                self.shelf[doi] = [title]
                new_additions.append( (doi, title) )
            else:
                title_list = self.shelf[doi]
                if title not in title_list:
                    title_list.append(title)
                    self.shelf[doi] = title_list
                    new_additions.append( (doi, title) )
        utc = datetime.datetime.utcnow()
        self.check_time = utc.strftime('%Y%m%d%H%M%S')
        print self.check_time
        print 'len new additions:', len(new_additions)
        print new_additions[:10]
        if new_additions:
            self.shelf.sync()
    
    def run_in_loop(self):
        while True:
            #print(str(datetime.datetime.now()))
            self.find_new_doi_article_pairs()


if __name__ == '__main__':
    getter = doi_finder(lang='test2wiki')
    getter.run_in_loop()