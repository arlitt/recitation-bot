# -*- coding: utf-8 -*-

import requests
from bs4 import BeautifulSoup
import wget
import urllib
import tarfile
import os
from subprocess import call
import xml.etree.ElementTree as etree
import pywikibot
from collections import defaultdict
from functools import wraps
import re
import ast
import pmc_extractor
import commons_template

class journal_article():
    '''This class represents a journal article 
    and its lifecycle to make it to Wikisource.'''
    def __init__(self, doi, static_vars):
        '''journal_articles are represented by dois'''
        if doi.startswith('http://dx.doi.org/'):
            doi_parts = doi.split('http://dx.doi.org/')
            doi = doi_parts[1] 
        self.doi = doi
        self.static_vars = static_vars
        #a phase is like, have we downloaded it, have we gotten the pmcid, uploaded the images etc.
        self.phase = defaultdict(bool)
    

    def get_pmcid(self):
        idpayload = {'ids' : self.doi, 'format': 'json'}
        idconverter = requests.get('http://www.pubmedcentral.nih.gov/utils/idconv/v1.0/', params=idpayload)
        records = idconverter.json()['records']
        try:
            if len(records) == 1:
                #since we are supplying a single doi i believe we should be getting only 1 record
                record = records[0]
            else:
                raise ConversionError(message='not just one pmcid for a doi',doi=self.doi)
            self.pmcid = record['pmcid'] 
            self.phase['get_pmcid'] = True           
        except:
            raise ConversionError(message='cannot get pmcid',doi=self.doi)

    
    def get_targz(self):
        try:
            archivefile_payload = {'id' : self.pmcid}
            archivefile_locator = requests.get('http://www.pubmedcentral.nih.gov/utils/oa/oa.fcgi', params=archivefile_payload)
            record = BeautifulSoup(archivefile_locator.content)

            # parse response for archive file location
            archivefile_url = record.oa.records.record.find(format='tgz')['href']

            archivefile_name = wget.filename_from_url(archivefile_url)
            complete_path_targz = os.path.join(self.static_vars["data_dir"], archivefile_name)
            urllib.urlretrieve(archivefile_url, complete_path_targz)
            self.complete_path_targz = complete_path_targz

             # @TODO For some reason, wget hangs and doesn't finish, using
             # urllib.urlretrieve() instead for this for now.
             # archivefile = wget.download(archivefileurl, wget.bar_thermometer)
            self.phase['get_targz'] = True
        except:
            raise ConversionError(message='could not get the tar.gz file from the pubmed', doi=self.doi)


    def extract_targz(self):
        try:
            directory_name, file_extension = self.complete_path_targz.split('.tar.gz')
            self.article_dir = directory_name
            tar = tarfile.open(self.complete_path_targz, 'r:gz')
            tar.extractall(self.static_vars["data_dir"])
            self.phase['extract_targz'] = True
        except:
            raise ConversionError(message='trouble extracting the targz', doi=self.doi)
    

    def find_nxml(self):
        try:
            self.qualified_article_dir = os.path.join(self.static_vars["data_dir"], self.article_dir)
            nxml_files = [file for file in os.listdir(self.qualified_article_dir) if file.endswith(".nxml")]
            if len(nxml_files) != 1:
                raise ConversionError(message='we need excatly 1 nxml file, no more, no less', doi=self.doi)
            nxml_file = nxml_files[0]
            self.nxml_path = os.path.join(self.qualified_article_dir, nxml_file)
            self.phase['find_nxml'] = True
        except ConversionError as ce:
            raise ce
        except:
            raise ConversionError(message='could not traverse the search dierctory for nxml files', doi=self.doi)
        
    def extract_metadata(self):
        self.metadata = pmc_extractor.extract_metadata(self.nxml_path)
        self.phase['extract_metadata'] = True

    
    def xslt_it(self):
        try:
            doi_file_name = self.doi + '.mw.xml'
            mw_xml_file = os.path.join(self.static_vars["data_dir"], doi_file_name)
            doi_file_name_pre_slash = doi_file_name.split('/')[0]
            if doi_file_name_pre_slash == doi_file_name:
                raise ConversionError(message='i think there should be a slash in the doi', doi=self.doi)
            mw_xml_dir = os.path.join(self.static_vars["data_dir"], doi_file_name_pre_slash)
            if not os.path.exists(mw_xml_dir):
                os.makedirs(mw_xml_dir)
            mw_xml_file_handle = open(mw_xml_file, 'w')
            call_return = call(['xsltproc', self.static_vars["jats2mw_xsl"], self.nxml_path], stdout=mw_xml_file_handle)
            if call_return == 0: #things went well
                mw_xml_file_handle.close()
                self.mw_xml_file = mw_xml_file
                self.phase['xslt_it'] = True
            else:
                raise ConversionError(message='something went wrong during the xsltprocessing', doi=self.doi)
        except:
            raise ConversionError(message='something went wrong, probably munging the file structure', doi=self.doi)
    

    def get_mwtext_element(self):
        try:
            tree = etree.parse(self.mw_xml_file)
            root = tree.getroot()
            mwtext = root.find('mw:page/mw:revision/mw:text', namespaces={'mw':'http://www.mediawiki.org/xml/export-0.8/'})
            self.wikitext = mwtext.text
            self.phase['get_mwtext_element'] = True
        except:
            raise ConversionError(message='no text element')

    def get_mwtitle_element(self):
        try:
            tree = etree.parse(self.mw_xml_file)
            root = tree.getroot()
            mwtitle = root.find('mw:page/mw:title', namespaces={'mw':'http://www.mediawiki.org/xml/export-0.8/'})
            self.title = mwtitle.text
            self.metadata['title'] = mwtitle.text
            self.phase['get_mw_title_element'] = True
        except:
            raise ConversionError(message='mw_title_element not found')
        


    def upload_images(self):
        #want to make the name commons-compatible in the way that OAMI does
        def harmonizing_name(image_name, article_title):
            '''Copy Pasta-ed from open access media importer to get it the same'''
            dirty_prefix = article_title
            dirty_prefix = dirty_prefix.replace('\n', '')
            dirty_prefix = ' '.join(dirty_prefix.split()) # remove multiple spaces
            forbidden_chars = u"""?,;:^/!<>"`'±#[]|{}ʻʾʿ᾿῾‘’“”"""
            for character in forbidden_chars:
                dirty_prefix = dirty_prefix.replace(character, '')
            # prefix is first hundred chars of title sans forbidden characters
            prefix = '-'.join(dirty_prefix[:100].split(' '))
            # if original title is longer than cleaned up title, remove last word
            if len(dirty_prefix) > len(prefix):
                prefix = '-'.join(prefix.split('-')[:-1])
            if prefix[-1] != '-':
                prefix += '-'
            return prefix + image_name
        
        #get all the images in our folder with valid extension
        self.image_files = [image_file for image_file in os.listdir(self.qualified_article_dir) if \
               any([image_file.endswith(extension) for extension in self.static_vars['image_extensions'] ] ) ]
        
        commons = pywikibot.Site('commons', 'commons')
        if not commons.logged_in():
            commons.login()
        #commons = pywikibot.Site('test2', 'wikipedia')
        
        #our data type will be a dict with the jpg and what it ended-up being called on commons
        
        self.used_image_names = dict()

        for image_file in self.image_files:
            qualified_image_location = os.path.join(self.qualified_article_dir, image_file)
            harmonized_name = harmonizing_name(image_file, self.title)
            #print harmonized_name
            image_page = pywikibot.ImagePage(commons, harmonized_name)
            page_text = commons_template.page(self.metadata)
            image_page._text = page_text
            try:
                commons.upload(imagepage=image_page, source_filename=qualified_image_location, comment='Automatic upload of media from: [[doi:' + self.doi+']]')
                self.used_image_names[image_file] = harmonized_name
            except pywikibot.exceptions.UploadWarning as warning:
                warning_string = unicode(warning)
                if warning_string.startswith('Uploaded file is a duplicate of '):
                    liststring = warning_string.split('Uploaded file is a duplicate of ')[1][:-1]
                    duplicate_list = ast.literal_eval(liststring)
                    duplicate_name = duplicate_list[0]
                    print 'duplicate found: ', duplicate_name
                    self.used_image_names[image_file] = duplicate_name
                elif warning_string.endswith('already exists.'):
                    self.used_image_names[image_file] = harmonized_name
                else:
                    raise
        self.phase['upload_images'] = True

                
    def replace_image_names_in_wikitext(self):
        
        image_names_without_extension = {image_file: os.path.splitext(image_file)[0] for image_file in self.used_image_names.iterkeys()}
        
        replacing_text = self.wikitext
        for image_file, image_name_without_extension in image_names_without_extension.iteritems():
            extensionless_re = r'File:(' + image_name_without_extension + r')\|'
            new_file_text = r'File:' + self.used_image_names[image_file] + r'|'
            replacing_text, occurences = re.subn(extensionless_re, new_file_text, replacing_text)
            if occurences < 1:
                print occurences, image_file, image_name_without_extension
            if occurences > 1:
                # see if it has the .jpg already baked i
                print occurences, image_file, image_name_without_extension
        #print replacing_text
        self.image_fixed_wikitext = replacing_text

        self.phase['replace_image_names_in_wikitext'] = True
                        

    def push_to_wikisource(self):
        site = pywikibot.Site(self.static_vars["wikisource_site"], "wikisource")
        #site = pywikibot.Site('test2', "wikipedia")
        page = pywikibot.Page(site, self.static_vars["wikisource_basepath"] + self.title)
        #page = pywikibot.Page(site, 'Wikipedia:DOIUpload/' + self.title)
        comment = "Imported from [[doi:"+self.doi+"]] by recitationbot"
        page.put(newtext=self.image_fixed_wikitext, botflag=True, comment=comment)
        self.wiki_link = page.title(asLink=True)
        self.phase['push_to_wikisource'] = True
    

    def push_redirect_wikisource(self):
        site = pywikibot.Site(self.static_vars["wikisource_site"], "wikisource")
        page = pywikibot.Page(site, self.static_vars["wikisource_basepath"] + self.doi)
        comment = "Making a redirect"
        redirtext = '#REDIRECT [[' + self.static_vars["wikisource_basepath"] + self.title +']]'
        page.put(newtext=redirtext, botflag=True, comment=comment)
        self.phase['push_redirect_wikisource'] = True


class ConversionError(Exception):
    def __init__(self, message, doi):
        # Call the base class constructor with the parameters it needs
        Exception.__init__(self, message)
        # Now for your custom code...
        self.error_doi = doi