# chaincrawler
a python implementation of a crawler for ChainAPI (HAL/JSON implementation).

##**chaincrawler** is part of [LearnAir, a master's thesis](https://www.davidbramsay.com/learnair).

**code written for LearnAir includes:**
+ [chainCrawler and chainSearcher](https://github.com/dramsay9/chaincrawler) - *a web crawler and a breadth-first-search tool for the semantic web data achitecture ChainAPI*
+ [chainTraverser and chainDataPush](https://github.com/dramsay9/chainlearnairdata) - *a stateful web spider to traverse, upload, modify, and interact with ChainAPI nodes and data, including pushing data from Excel files*
+ [chainProcessor](https://github.com/dramsay9/chaindataprocessor) - *a scalable machine learning crawler framework, which automatically crawls and downloads data from a list of 'known' device types in ChainAPI, processes their data using a device-specific model (that automatically updates when new data is found), and uploads that processed data back into ChainAPI*
+ [an Air Quality Ontology Adaptation of Chain API](https://github.com/dramsay9/chain-api) ([original tool](https://github.com/ResEnv/chain-api) written by Spencer Russel et al) - *air quality data ontology written with ChainAPI- a semantic web, RESTful Sensor API*

Additional resources include:
+ [the thesis document](https://davidbramsay.com/public/RamsayMastersThesis.pdf) (full documentation/motivation, esp. Chap 6. ChainAPI for Air Quality)
+ [the repo for the thesis document](https://github.com/dramsay9/thesis)
+ [jupyter notebooks used in data pre-processing, machine learning, and plot generation](https://github.com/dramsay9/learnair-data-crunching)(with raw data)
+ [a quick video introducing the learnAir concept](https://vimeo.com/188586371)
+ [the original ChainAPI project](https://github.com/ResEnv/chain-api)


##relevant thesis excerpt:

### ChainCrawler

Now that we’ve created an ontology for air quality, it’s important to have the tools to interact with the data as new devices are added. ChainCrawler is a tool for crawling through ChainAPI resource links and discovering new resources. It works like a traditional web- crawler.

ChainCrawler is highly optimized for speed and scale, using Google’s CityHash to track the most recently visited resources so the crawler doesn’t loop or backtrack. It has the additional feature of tracking hash collisions as required, and can accept any power of 2 size hash table.

ChainCrawler accepts an entry point URI, and picks a random, un- explored link to traverse from that resource. If it reaches a dead end or has already visited all of a resource’s links, it moves back through its recent history ( of URIs in history are definable) to look for unex- plored resources. If it runs out of history, it returns to the entry-point resource. At this point, if every entry-point resource path has been visited, the cache is cleared and the process is started over.
ChainCrawler will return the URI(s) of chain resources based on search criteria. It can filter on resource_type (i.e. ’Site’ or ’Device’), resource_title (i.e. ’Site 1- Roxbury’ or ’Device 2’), any arbitrary object attribute, or any combination of the above.
ChainCrawler can be used in several modes. It can be run in a block- ing manner, and simply return the URI of the first resource it finds. It can be run as a separate thread, and pass URIs to another thread us- ing python’s ’Queue’ library. It can also be run in ZMQ push mode, in which case all URIs are pushed out over a ZMQ socket using push/pull (preferred method). In these threaded cases, chainCrawler will not stop crawling until forced. It will not return duplicate re- sources for over a given, user-definable, refractory period (which can be set to infinite).

'''
**chainCrawler.ChainCrawler**(entry_point, cache_table_mask_length, track_search_depth, found_set_persistence, crawl_delay, filter_keywords)
	
	*Initialize a ChainCrawler Instance.*

	**entry_point** ( = 'http://learnair.media.mit.edu:8000') is the URI of the resource to start crawling.
	**cache_table_mask_length** ( = 8) is the exponent used to define the hash mask for the hash table. (hash table size = 2^cache_table_mask_length)
	**search_depth** ( = 5) is the number of URIs we store in history, in case we exhaust all links and have to back up.
	**found_set_persistence** ( = 720) is the time, in minutes, that a crawler will remember a resource it has already seen, and will not re-push it to the user 
	**crawl_delay** ( = 1000) is the time, in ms, in between calls to the server to access chain resources.
	**filter_keywords** ( = ['next','previous']) is an array of link relationships we want to ignore while crawling.  For our data, 'next' and 'previous' are required.


**chainCrawler.ChainCrawler.find**(namespace, resource_type, plural_resource_type, resource_title, resource_extra)
	
	*Blocking crawl that will exit/return the URI of the first matching resource.*

	**namespace** ( = "") is the base URI that defines the ontological relationships.  Prepended to resource_types. 
	**resource_type** ( = None) is an optional resource type search criteria that must match a given resource for it to be returned.
	**plural_resource_type** ( = None) is a search criteria that should correspond the resource_type field. Plural types are automatically generated by looking at the singular resource_type and adding 's' and 'es', but for words that have strange pluralization, it is important to give the correct plural form. 
	**resource_title** ( = None) is an optional resource title search criteria that must match a given resource for it to be returned.
	**resource_extra** ( = None) is an optional dictionary of attribute:value pairs that must match a given resource for it to be returned.


**chainCrawler.ChainCrawler.crawl_thread**(q, namespace, resource_type, plural_resource_type, resource_title, resource_extra)
	
	*Similar to find, but this function spins up a background thread crawler that will push URIs of the matching resources* 
	*onto the queue 'q'.*

	**q** ( = None) is the Queue object that URIs will be pushed to for other python threads to access. 


**chainCrawler.ChainCrawler.crawl_zmq**(socket, namespace, resource_type, plural_resource_type, resource_title, resource_extra)
	
	*Similar to find, but this function spins up a background thread crawler that will push URIs of the matching resources*
	*over a PUSH/PULL ZMQ socket.*

	**socket** ( = 'tcp://127.0.0.1:5557') is the ZMQ PUSH/PULL socket that URIs will be pushed to for other programs to access. 
'''

### 
