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
from leakyLIFO import LeakyLIFO
from crawlerCache import CrawlerCache
from globalConfig import log
import re
import time
import requests
import Queue


class ChainCrawler(object):


    def __init__(self, entry_point='http://learnair.media.mit.edu:8000/', \
            cache_table_mask_length=8, track_search_depth=5, crawl_delay=1000):

        self.entry_point = entry_point #entry point URI

        #initialize crawl variables
        self.current_uri = entry_point #keep track of current location
        self.current_uri_type = 'entry_point'
        self.crawl_history = LeakyLIFO(track_search_depth) #keep track of past
        self.crawl_delay = crawl_delay #in milliseconds

        #initialize cache
        self.cache = CrawlerCache(cache_table_mask_length)

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
                        log.info( 'CURIES: %s moved to %s', key, newIndex )

            #delete curies section of json if desired
            if del_curies:
                del json['_links']['curies']
                log.info( 'CURIES: CURIES Resource applied fully & removed.' )

        except:
            log.warn( "CURIES: No CURIES found" )

        return json


    @staticmethod
    def pluralize_resource_name(resource_name, namespace=""):
        return [namespace + resource_name + 's', namespace + resource_name + 'es']

    @staticmethod
    def get_external_link_array(req_links):
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
                        items_item['type'] = self.crawl_history.asList[-1]['type']
                    except:
                        log.error('Cannot inherit type information of list from previous crawl')
                        items_item['type'] = 'UNKNOWN'
                    items_item['from_item_list'] = True
                    crawl_links.append(items_item)

            #now filter out links we don't want and push the rest
            elif not any(substring in key.lower() for substring in \
                    ['edit','create','self','curies','websocket']):
                if item is not None:
                    item['type']=key
                    item['from_item_list'] = False
                    crawl_links.append(item)
                else:
                    log.warn(' EXTRACT_LINK: nonetype link detected in' + \
                            ' resource %s', key)

        return crawl_links


    def get_external_links(self, json):

        #call 'real' function, which (1) flattens 'items', (2) filters out
        #create/edit forms, websockets, curies, and self, and (3) formats
        #things nicely for us in an array:
        crawl_links = self.get_external_link_array(json)

        #we now have a well-structured list of links with known types
        #before returning, delete any list items that are in our crawl history
        crawl_links = [x for x in crawl_links if x not in (y['href'] for y in self.crawl_history.asList())]

        #for our final list, check to see which links are in cache
        for link in crawl_links:
            link['in_cache'] = self.cache.check(link['href'])

        return crawl_links


#crawl() -> push queue of each uri/resource crawled
#crawl(namespace, resource_type, plural_resource_type, resource_title, resource_links)
#        -> push queue of each uri/resource matching all criteria


    def crawl(self, namespace="", resource_type=None, plural_resource_type=None,\
            resource_title=None, resource_links=None):

        loop_count=0

        #keep calling crawl_node, unless it returns false, with a pause between
        while(self.crawl_node(namespace,resource_type, plural_resource_type,\
                resource_title, resource_links)):

            #delay for crawl_delay ms between calls
            time.sleep(self.crawl_delay/1000.0)

            #count loop iterations
            loop_count = loop_count + 1
            log.debug( "MAIN CRAWL LOOP ITERATION %s -----------------", loop_count )

        log.info( "--- crawling ended, %s pages crawled ---", loop_count )


    def crawl_node(self, namespace="", resource_type=None, \
            pluralization=None, resource_title=None, resource_link_types=None):
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
        title of the resource.

        if you're not interested in defining the resource type, but only want
        resources that link to certain things, the resource_links field will
        allow you to specify what resources you want to have valid links to.
        I.E. a device linked to a site, as opposed to a device linked to a
        deployment.
        '''

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

        log.debug('HAL/JSON LINKS CURIES APPLIED, FILTERED (for history,' + \
                'self, create/edit, ws, itemlist flattened): %s', crawl_links)

        #queue up uris/resources that match search criteria!
        #(1) if resource name exists, filter items to  get only items that
        #match the singular resource name, AND (things that match the plural
        #resource name && are from_item_list)
        for link_item in crawl_links:

        #(2) if title exists, filter items remaining for those that match the title
        #(3) if links exist, filter items remaining for those that have matching links

        #search for case-insensitive singular, and plural +'s' +'es', add field to give plural name
        #if items, iterate through, use 'type' of previous step w/title


        #push all matching items out to queue

        #select next link

        #push into history
        self.crawl_history.push({'href':self.current_uri, 'type':self.current_uri_type})

        #update current_uri and current_uri_type

        #recurse
        return True
'''
-get links to eternal resources, eliminate any in depth memory (where you came from)
-compare against search criteria, push matching to external queue
-randomly select one resource link if any exist, compare against hashes, if not hashed follow
-if hashed, select from remaining links and compare, if not hashed follow. repeat until all exhausted
-if all hashed from current resource, move back up depth memory one resource and repeat
-if we exhaust full depth history and all are hashed, go back to entrypoint and start over
-if we are at the entrypoint and try to go back, clear hash table
-delay between access
'''

if __name__=="__main__":
    crawler = ChainCrawler('http://learnair.media.mit.edu:8000/devices/10')
    #crawler = ChainCrawler('http://learnair.media.mit.edu:8000/devices/?site_id=1')
    #crawler = ChainCrawler()
    crawler.crawl()
