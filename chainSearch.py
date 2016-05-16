#!/usr/bin/python
'''
This is a webcrawler for Chain-API. (https://.github.com/ResEnv/chain-api)

This is based on the ChainCrawler (see that file), but instead of continuing
to crawl and being designed to crawl, this will simply find and return the
uri of a resource without continuing to crawl.  No ZMQ, no Queue/Asyc support.

There are three main find modes: find just quits and returns the URI of the
first match.  find_degrees_all will do an exhaustive breadth first search
of x degrees, and when completed will return a list of all matches.
find_create_link will do a similar exhaustive search and return a create link
for a particular type of object related to the starting resource.

'''

from crawlerCache import CrawlerCacheWithCollisionHistory
from leakyLIFO import LeakyLIFO
from timeDecaySet import TimeDecaySet
from globalConfig import log
import re
import time
import random
import requests
import threading
import Queue
import zmq


class ChainSearch(object):


    def __init__(self, entry_point='http://learnair.media.mit.edu:8000/', \
            crawl_delay=1000, filter_keywords=['previous','next']):
        #entry_point = starting URL for crawl
        #search_depth = how many steps in path we save to retrace when at a dead end
        #found_set_persistence = how long, in min,  to keep a resource URI in memory
        #       before it is allowed to be returned as a new resource again.  720= 12
        #       hours before crawler 'forgets' it has seen something and resubmits it
        #       in the queue to be processed
        #crawl_delay = how long, in ms, before accessing/crawling a new resource

        self.entry_point = entry_point #entry point URI

        #initialize crawl variables
        self.current_uri = entry_point #keep track of current location
        self.current_uri_type = 'entry_point'
        self.crawl_delay = crawl_delay #in milliseconds
        self.degrees = 0
        self.return_if_found = False
        self.createform_type = None

        self.found_resources = TimeDecaySet(0)

        #initialize filter word list for crawling
        self.filter_keywords = ['edit','create','self','curies','websocket']
        [self.filter_keywords.append(x) for x in filter_keywords]
        log.debug( "filter keywords %s", self.filter_keywords)

        log.info( "-----------------------------------------------" )
        log.info( "Crawler Initialized." )
        log.info( "Entry Point: %s", self.entry_point )
        log.info( "-----------------------------------------------" )


    def reinit(self):

        self.current_uri = entry_point #keep track of current location
        self.current_uri_type = 'entry_point'
        self.degrees = 0
        self.return_if_found = False
        self.createform_type = None
        self.found_resources = TimeDecaySet(0)


    @staticmethod
    def apply_hal_curies(json, del_curies=True):
        '''Find and apply CURIES relationship shorcuts (namespace/rel
        definitions) to other links in the json object. I.E., if we have
        a CURIES "http://learnair.media.mit.edu/rels/{rel}" with name "ch",
        and a link further called 'ch:sites', remove the CURIES part of the
        object and apply it so that 'ch:sites' is now "http://learnair.media
        .mit.edu/rels/sites". del_curies tells this function whether to
        remove the CURIES section of _links after applying it to the document
        (True), or whether to leave it in (False).'''

        try:
            curies = json['_links']['curies'] #find the curies.

            for curie in curies: #compare each curies name...
                for key in json['_links']: #...with each link relationship

                    #if we find a link relation that uses the curies
                    if (key.startswith(curie['name'] + ':')):

                        #combine the curies & key to make the full resource link
                        newIndex = curie['href']
                        replaceString = key.split(curie['name'] + ':',1)[1]
                        newIndex = re.sub(r"\{.*\}", replaceString, newIndex)

                        #move the resource to the full resource link
                        json['_links'][newIndex] = json['_links'][key]
                        del json['_links'][key]
                        log.debug( 'CURIES: %s moved to %s', key, newIndex )

            #delete curies section of json if desired
            if del_curies:
                del json['_links']['curies']
                log.debug( 'CURIES: CURIES Resource applied fully & removed.' )

        except:
            log.warn( "CURIES: No CURIES found" )
            json['_links']={}

        return json


    @staticmethod
    def pluralize_resource_name(resource_name, namespace=""):
        return [namespace + resource_name + 's', namespace + resource_name + 'es']


    def flatten_filter_link_array(self, req_links):
        ''' takes a JSON array (after CURIES have been applied, if desired)
        and handles HAL 'items' collections and other links, by flattening
        them into a list.  each list element has list[0][fields] fields='href'
        (the actual crawlable link), 'type' (a link associated with the type
        at the other end of the link), 'from_item_list' (true if the resource
        was part of the item collection), and 'title' (a unique name for the
        resource on the other end of the link.

        'from_item_list' is required because collections inherit the type from
        the link above them, which is likely plural, even though they themselves
        are singular.  There is no generalizable way to go from a plural resource
        name to a singular one.  As such, 'from_item_list' tells us to accept the
        pluralized version of the type as indicitive of the found resource.
        '''
        crawl_links=[]

        #formulate and push link items to crawl_links array from json
        for key, item in req_links.iteritems():

            #first handle 'item' links
            if key == 'items':
                for items_item in item:
                    #inherit 'type' from previous crawl step
                    try:
                        items_item['type'] = self.current_uri_type
                    except:
                        log.error('Cannot inherit type information of list from previous crawl')
                        items_item['type'] = 'UNKNOWN'
                    items_item['from_item_list'] = True
                    crawl_links.append(items_item)

            #now filter out links we don't want and push the rest
            elif not any(substring in key.lower() for substring in \
                    self.filter_keywords):
                if item is not None:
                    item['type']=key
                    item['from_item_list'] = False
                    crawl_links.append(item)
                else:
                    log.warn(' EXTRACT_LINK: nonetype link detected in' + \
                            ' resource %s', key)

        return crawl_links


    def query_link_array(self, crawl_links):
        '''takes a crawl_link array (which has links and types of objects)
        and decides which of these links were quieried for. Return List of
        URIs that are matched resources not in the set already discovered'''

        if self.qry_resource_type is not None:
            log.info('SEARCH_LIST: looking for singular: %s', self.qry_resource_type)
            log.info('SEARCH_LIST: looking for plural as item_list: %s', self.qry_resource_plural)
        if self.qry_resource_title is not None:
            log.info('SEARCH_LIST: looking for title: %s', self.qry_resource_title)

        matching_uris = []

        #(1) if resource name exists, filter items to get only items that
        #match the singular resource name, AND (things that match the plural
        #resource name && are from_item_list)
        #(2) if title exists, filter items remaining for those that match the title

        for link_item in crawl_links:

            log.debug('SEARCH_LIST: checking if %s matches query criteria', link_item['href'])
            this_link_item_matches = True

            #see if it matches resource_type, if queried for
            if self.qry_resource_type is not None:
                if ((any(link_item['type'].lower() in x for x in self.qry_resource_plural) and link_item['from_item_list']) \
                        or (link_item['type'].lower() == self.qry_resource_type)):
                    #it does!

                    #double check for createForms the parent is correct
                    if ('createform' == link_item['type'].lower() and self.createform_type is not None):
                        if (self.current_uri_type not in self.createform_type):
                            this_link_item_matches = False
                        else:
                            log.info('SEARCH_LIST: matched search_type %s', link_item['type'])
                    else:
                        log.info('SEARCH_LIST: matched search_type %s', link_item['type'])

                else:
                    #it doesn't, but we're searching on resource_type
                    this_link_item_matches = False

            #see if it matches resource_title, if queried for
            if self.qry_resource_title is not None:
                if (link_item['title'].lower() == self.qry_resource_title):
                    #it does!
                    log.info('SEARCH_LIST: matched search_title %s', link_item['title'])
                else:
                    #it doesn't, but we're searching on resource_title
                    this_link_item_matches = False

            #if we made it to here and this_link_item_matches, it's a match!
            if this_link_item_matches:
                matching_uris.append(link_item['href'])

        #return list of matching uris
        return matching_uris


    def push_uris_to_queue(self, uris):
        '''check uris against found_resources set, and if they're not there,
        get resource and push URI and resource out to queue'''

        found_one = False
        #self.found_resources
        for uri in uris:
            #if 'add' returns true, it's not in our set yet
            if self.found_resources.add(uri):

                log.info('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
                log.info('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
                log.info('New Resource Found!  %s', uri)
                log.info('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
                log.info('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')

                found_one = True

        return found_one


    def search(self, namespace="", resource_type=None, \
            plural_resource_type=None, resource_title=None):
        '''
        crawl through chain, pushing uri/resource that match the passed criteria
        onto the queue.  If nothing is passed, push all resources.

        Can match the resource_type.  If you want a resource list (plural, i.e.
        lists of organizations resources NOT organization resources), you can
        specify that as the resource_type even though it is the plural.

        The code assumes the word can be pluralized by adding an 's' or 'es' to
        the end.  If this is not true (i.e. Person -> People) please give the
        plural so the code can recognize when it has found a list of the
        singular resource of interest.

        if looking for a specific resource, this will cross check against the
        title of the resource.  Selection will be ANDED with other query
        criteria.
        '''

        #store search criteria in lowercase form, with namespace appended
        #add plural forms +'s', +'es' to list of plural cases to look for

        if resource_type is not None and resource_type != 'createForm':
            #append namespace
            self.qry_resource_type = namespace + resource_type
            #make all lowercase
            self.qry_resource_type = self.qry_resource_type.lower()
            #'pluralize' resource after adding namespace
            self.qry_resource_plural = self.pluralize_resource_name(self.qry_resource_type)
            #add special pluralization if given by user
            if plural_resource_type is not None:
                self.qry_resource_plural.append(namespace + plural_resource_type)
            #make all plural list items lowercase
            self.qry_resource_plural = [x.lower() for x in self.qry_resource_plural]
        #check if we're searching for a createForm
        elif resource_type == 'createForm':
            #use this search criteria
            self.qry_resource_type = 'createform'
            self.qry_resource_plural = 'createform'
        else:
            #not searching on resource_type, just define qry_resource_type as None
            self.qry_resource_type = None

        if resource_title is not None:
            #make all lowercase
            self.qry_resource_title = resource_title.lower()
        else:
            #not searching on title, just define qry_resource_title as None
            self.qry_resource_title = None

        #end initializing query variables

        #initialize crawl variables
        self.current_uri = self.entry_point #keep track of current location
        self.current_uri_type = 'entry_point'

        self.bfs()

        return self.found_resources


    def bfs(self):

        current_depth = 0
        visited = set()
        link_tree = [[] for k in range(self.degrees)]

        while True:

            time.sleep(self.crawl_delay/1000.0)

            #download the current resource
            try:
                req = requests.get(self.current_uri)
                log.info( '%s downloaded.', self.current_uri )

                #put request in JSON form, apply CURIES, get links
                resource_json = req.json()
                log.debug('HAL/JSON RAW RESOURCE: %s', resource_json)

            #downloading the current resource failed
            except requests.exceptions.ConnectionError:

                log.warn( 'URI "%s" unresponsive, ignoring',\
                        self.current_uri )

                resource_json = {'_links':[]}

                #if we failed to download the entry point, give up
                if self.current_uri == self.entry_point:
                    log.error( 'URI is entry point, no previous link.  Try again when' \
                            + ' the entry point URI is available.' )
                    return

            #end downloading resource

            #get links from this resource
            req_links = self.apply_hal_curies(resource_json)['_links']
            crawl_links = self.flatten_filter_link_array(req_links)

            #crawl_links is a 'flat' list list[:][fields]
            #fields are href, type, title, in_cache, from_item_list

            log.debug('HAL/JSON LINKS CURIES APPLIED, FILTERED (for history,' + \
                    'self, create/edit, ws, itemlist flattened): %s', crawl_links)

            #find the uris/resources that match search criteria!
            matching_uris = self.query_link_array(crawl_links)
            #... and send them out!!
            if (self.push_uris_to_queue(matching_uris) and self.return_if_found):
                return #return if we are using find_first and we found one

            #push all uris that don't match visited to proper depth list
            visited.add(self.current_uri)

            if current_depth < self.degrees:
                [link_tree[current_depth].append(x) for x in crawl_links \
                        if not x['href'] in visited]

            log.debug('BFS Array: %s', link_tree)
            log.debug('VISITED: %s', visited)

            #select next current_uri and current_uri_type by looking through
            #link_tree, if empty return

            finished = True

            for index in range(len(link_tree)):
                if len(link_tree[index]):

                    self.current_uri = link_tree[index][0]['href']
                    self.current_uri_type = link_tree[index][0]['type']
                    del link_tree[index][0]

                    current_depth = index + 1
                    finished = False
                    break

            if finished:
                return

            log.debug('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
            log.info('CRAWL: moving to %s', self.current_uri)
            log.info('CRAWL: type: %s', self.current_uri_type)
            log.info('CRAWL: depth: %s', current_depth)
            log.debug('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')


    def find_degrees_all(self, namespace="", resource_type=None, \
            plural_resource_type=None, resource_title=None, degrees=1):
        '''only looks at 'degrees' degree away for the resources exhaustively,
        returns the list after examining all links 'degrees' away'''
        self.reinit()
        self.degrees = degrees

        return self.search(namespace=namespace, resource_type=resource_type, \
            plural_resource_type=plural_resource_type, resource_title=resource_title).asList()


    def find_first(self, namespace="", resource_type=None, \
            plural_resource_type=None, resource_title=None, max_degrees=3):
        '''breadth first search, returning first matching resource.  Max_degrees
        specifies the max degrees of seperation it will exhaustively search
        before giving up and returning an empty list if none are found'''
        self.reinit()
        self.degrees = max_degrees
        self.return_if_found = True

        return self.search(namespace=namespace, resource_type=resource_type, \
            plural_resource_type=plural_resource_type, resource_title=resource_title).asList()


    def find_create_link(self, namespace="", resource_type=None, \
            plural_resource_type=None, degrees=1):
        ''' look for a createform link of type resource_type, at most 'degrees'
        degrees away from the entrypoint, and return after exhaustive search'''

        self.filter_keywords = [x for x in self.filter_keywords if x != 'create']
        self.degrees = degrees
        self.return_if_found = False

        print self.filter_keywords

        if resource_type is not None:
            #append namespace
            self.createform_type = namespace + resource_type
            #make all lowercase
            self.createform_type = [self.createform_type.lower()]
            #'pluralize' resource after adding namespace
            [self.createform_type.append(x) for x in \
                    self.pluralize_resource_name(self.createform_type[0])]

        found_link= self.search(namespace=namespace, resource_type='createForm', \
            plural_resource_type=plural_resource_type).asList()

        self.filter_keywords.append('create')

        return found_link


    def reset_entrypoint(self, new_entrypoint = 'http://learnair.media.mit.edu:8000/'):
        self.entry_point = new_entrypoint #entry point URI
        self.current_uri = new_entrypoint #keep track of current location
        self.current_uri_type = 'entry_point'


if __name__=="__main__":


    #######JUST CRAWL EXAMPLES######

    searcher = ChainSearch('http://learnair.media.mit.edu:8000/devices/10')

    #x = searcher.find_degrees_all(namespace='http://learnair.media.mit.edu:8000/rels/', \
    #       resource_title='a') #resource_type='site')
    x = searcher.find_create_link(namespace='http://learnair.media.mit.edu:8000/rels/')#, \
    #       resource_type='sensor') #resource_type='site')
    print '---------------------'
    print x

    #searcher = ChainSearch('http://learnair.media.mit.edu:8000/devices/?site_id=1')

    #crawler.crawl(namespace='http://learnair.media.mit.edu:8000/rels/', \
    #        resource_title='a')
    #crawler.crawl(namespace='http://learnair.media.mit.edu:8000/rels/', \
    #        resource_type='Device', \
    #        resource_title='test004')
    #crawler.crawl()




