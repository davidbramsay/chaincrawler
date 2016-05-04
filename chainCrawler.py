#!/usr/bin/python
'''
This is a webcrawler for Chain-API. (https://.github.com/ResEnv/chain-api)

To make sure it doesn't revisit URIs, it creates a hash table where it stores
64 bit hashes for the URI, created by google's fast hash cityHash64.  This is
indexed by the last several digits of the hash.  When a hash collision occurs,
the new hash value simply overwrites the previous.  Locality of URIs should
allow this to work for storing non-colliding hashes of the most recent URIs.

ex:  'http://test.com' hashes to '0x1234567887654321', and the cache table size
is 2^8, or 256, so we apply an 8 bit mask of 0xff (& 255) to the hash. This
gives us hashtable[0x21] = 0x1234567887654321.  Whenever we touch a new URI,
we check the masked portion of the URI's hash to see if it matches the stored
hash.  If it does, we skip it.  If it doesn't or it doesn't exist, we crawl
the page and overwrite the hash value there.

Hash Table and Algorithm have been optimized with external C libraries for
size and speed, and preallocated.

TODO: restructure for parallelism:
 -SHARED CACHE OF VISITS
 -IF CACHE DOESN'T CHANGE FOR A LONG TIME, CLEAR
 -MAIN ENTRYPOINT-> SPIN UP SEVERAL CONCURRENT CRAWLERS (set #)
 -EACH CRAWLER UPDATES ENTRYPOINT
 -EACH CRAWLER DEPTH FIRST SEARCH WITH SOME MAX DEPTH STACK, FILO, IF FINISHED
  AND NOT POPPING ENTRYPOINT, GO BACK TO ENTRYPOINT AND START AGAIN.
 -IF ALL CHILD SITES VISITED, RANDOMLY PICK ONE.


depth first search with given depth 'memory'
expose queue of resources/links to matching rel namespace and resource type

-get links to eternal resources, eliminate any in depth memory (where you came from)
-compare against search criteria, push matching to external queue
-randomly select one resource link if any exist, compare against hashes, if not hashed follow
-if hashed, select from remaining links and compare, if not hashed follow. repeat until all exhausted
-if all hashed from current resource, move back up depth memory one resource and repeat
-if we exhaust full depth history and all are hashed, go back to entrypoint and start over
-if we are at the entrypoint and try to go back, clear hash table
-delay between access

'''
from crawlerCache import CrawlerCacheWithCollisionHistory
from leakyLIFO import LeakyLIFO
from timeDecaySet import TimeDecaySet
from globalConfig import log
import re
import time
import random
import requests
import Queue


class ChainCrawler(object):


    def __init__(self, entry_point='http://learnair.media.mit.edu:8000/', \
            cache_table_mask_length=8, track_search_depth=5, \
            found_set_persistence=720, crawl_delay=1000, filter_keywords=['previous','next']):
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
        self.crawl_history = LeakyLIFO(track_search_depth) #keep track of past
        self.crawl_delay = crawl_delay #in milliseconds
        self.found_resources = TimeDecaySet(found_set_persistence) #in seconds

        #initialize cache
        self.cache = CrawlerCacheWithCollisionHistory(cache_table_mask_length)

        #initialize filter word list for crawling
        self.filter_keywords = ['edit','create','self','curies','websocket']
        [self.filter_keywords.append(x) for x in filter_keywords]
        log.debug( "filter keywords %s", self.filter_keywords)

        log.info( "-----------------------------------------------" )
        log.info( "Crawler Initialized." )
        log.info( "Entry Point: %s", self.entry_point )
        log.info( "-----------------------------------------------" )


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


    def get_external_links(self, req_links):

        #call 'real' function, which (1) flattens 'items', (2) filters out
        #create/edit forms, websockets, curies, and self, and (3) formats
        #things nicely for us in an array:
        crawl_links = self.flatten_filter_link_array(req_links)

        #we now have a well-structured list of links with known types
        #before returning, delete any list items that are in our crawl history
        crawl_links = [x for x in crawl_links if x not in (y['href'] for y in self.crawl_history.asList())]

        #for our final list, append info on whether links are in cache
        for link in crawl_links:
            link['in_cache'] = self.cache.check(link['href'])

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
        #self.found_resources
        for uri in uris:
            #if 'add' returns true, it's not in our set yet
            if self.found_resources.add(uri):

                log.info('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
                log.info('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
                log.info('New Resource Found!  %s', uri)
                log.info('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
                log.info('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')

                #get resource associated with uri

                #push uri and resource to queue!


    def crawl(self, namespace="", resource_type=None, \
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

        if resource_type is not None:
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

        loop_count=0

        #keep calling crawl_node, unless it returns false, with a pause between
        while(self.crawl_node()):

            #delay for crawl_delay ms between calls
            time.sleep(self.crawl_delay/1000.0)

            #count loop iterations
            loop_count = loop_count + 1
            log.info( "MAIN CRAWL LOOP ITERATION %s -----------------", loop_count )

        log.info( "--- crawling ended, %s pages crawled ---", loop_count )


    def crawl_node(self):

        #put uri in cache now that we're crawling it, make a note of collisions
        if self.cache.put_and_collision(self.current_uri):
            log.info( 'HASH COLLISION: value overwritten in hash table.' )

        #debug: print state of cache after updating
        log.debug('CACHE STATE: %s', self.cache._cache)

        #download the current resource
        try:
            req = requests.get(self.current_uri)
            log.info( '%s downloaded.', self.current_uri )

        #downloading the current resource failed
        except requests.exceptions.ConnectionError:

            log.warn( 'URI "%s" unresponsive, moving back to previous link...',\
                    self.current_uri )

            #if we failed to download the entry point, give up
            if self.current_uri == self.entry_point:
                log.error( 'URI is entry point, no previous link.  Try again when' \
                        + ' the entry point URI is available.' )
                return False

            #if it wasn't the entry point, go back in our search history
            try:
                prev = self.crawl_history.pop()
                self.current_uri = prev['href']
                self.current_uri_type = prev['type']
                return True

            #if we don't have any history left, go back to the entry point
            except:
                log.info( 'exhausted depth of search history, back to entry point' )
                self.current_uri = self.entry_point
                self.current_uri_type = "entry_point"
                return True

        #end downloading resource

        #put request in JSON form, apply CURIES, get links
        resource_json = req.json()
        log.debug('HAL/JSON RAW RESOURCE: %s', resource_json)

        req_links = self.apply_hal_curies(resource_json)['_links']
        crawl_links = self.get_external_links(req_links)

        #crawl_links is a 'flat' list list[:][fields]
        #fields are href, type, title, in_cache, from_item_list

        log.debug('HAL/JSON LINKS CURIES APPLIED, FILTERED (for history,' + \
                'self, create/edit, ws, itemlist flattened): %s', crawl_links)

        #find the uris/resources that match search criteria!
        matching_uris = self.query_link_array(crawl_links)
        #... and send them out!!
        self.push_uris_to_queue(matching_uris)

        #select next link!!!!

        #get uncached links
        uncached_links = [x for x in crawl_links if not x['in_cache']]
        log.info('CRAWL: %s LINKS UNCACHED OF %s LINKS FOUND', \
                len(uncached_links), len(crawl_links) )

        if (len(uncached_links)>0):
            #we have uncached link(s) to follow! randomly pick one.
            random_index = random.randrange(0,len(uncached_links))

            self.crawl_history.push({'href':self.current_uri, 'type':self.current_uri_type})
            self.current_uri = uncached_links[random_index]['href']
            self.current_uri_type = uncached_links[random_index]['type']

        else:
            #we don't have any uncached options from this node. Damn.
            log.info('CRAWL: no new links available here, crawling back up history')

            #special case of being at the entry point
            if (self.current_uri_type == 'entry_point'):
                #double check we have something to crawl
                if (len(crawl_links) > 0):

                    log.info('CRAWL: no uncached links from entrypoint, resetting cache')
                    self.cache.clear() # clear cache

                    #randomly select node from crawl_links
                    random_index = random.randrange(0,len(crawl_links))

                    self.crawl_history.push({'href':self.current_uri, 'type':self.current_uri_type})
                    self.current_uri = crawl_links[random_index]['href']
                    self.current_uri_type = crawl_links[random_index]['type']

                else:
                    log.error('CRAWL: NO CRAWLABLE LINKS DETECTED AT ENTRY_POINT!!!!')
                    return False

            #not at entry point, time to try and move back up in history
            try:
                prev = self.crawl_history.pop()
                self.current_uri = prev['href']
                self.current_uri_type = prev['type']

            except: #no history left, not at entry point- jump to entry point
                log.info('CRAWL: crawling back up history, but exhausted history.  Jump to entrypoint.')
                self.current_uri= self.entry_point
                self.current_uri_type = 'entry_point'

        log.debug('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')
        log.info('CRAWL: crawling to %s', self.current_uri)
        log.info('CRAWL: type: %s', self.current_uri_type)
        log.debug('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<')

        #recurse
        return True


if __name__=="__main__":

    #crawler = ChainCrawler('http://learnair.media.mit.edu:8000/devices/10')
    #crawler = ChainCrawler('http://learnair.media.mit.edu:8000/devices/?site_id=1')
    crawler = ChainCrawler(found_set_persistence=2, crawl_delay=500)
    #crawler = ChainCrawler()

    #crawler.crawl(namespace='http://learnair.media.mit.edu:8000/rels/', \
    #        resource_type='site')
    #crawler.crawl(namespace='http://learnair.media.mit.edu:8000/rels/', \
    #        resource_title='a')
    crawler.crawl(namespace='http://learnair.media.mit.edu:8000/rels/', \
            resource_type='Device', \
            resource_title='test004')
    #crawler.crawl()
