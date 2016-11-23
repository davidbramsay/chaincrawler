# chaincrawler
a python implementation of a crawler for the Chain-API (HAL/JSON implementation)

chaincrawler is part of LearnAir, a master's thesis.

Now that we’ve created an ontology for air quality, it’s important to have the tools to interact with the data as new devices are added. ChainCrawler is a tool for crawling through ChainAPI resource links and discovering new resources. It works like a traditional web- crawler.
ChainCrawler is highly optimized for speed and scale, using Google’s CityHash to track the most recently visited resources so the crawler doesn’t loop or backtrack. It has the additional feature of tracking hash collisions as required, and can accept any power of 2 size hash table.
ChainCrawler accepts an entry point URI, and picks a random, un- explored link to traverse from that resource. If it reaches a dead end or has already visited all of a resource’s links, it moves back through its recent history ( of URIs in history are definable) to look for unex- plored resources. If it runs out of history, it returns to the entry-point resource. At this point, if every entry-point resource path has been visited, the cache is cleared and the process is started over.
ChainCrawler will return the URI(s) of chain resources based on search criteria. It can filter on resource_type (i.e. ’Site’ or ’Device’), resource_title (i.e. ’Site 1- Roxbury’ or ’Device 2’), any arbitrary object attribute, or any combination of the above.
ChainCrawler can be used in several modes. It can be run in a block- ing manner, and simply return the URI of the first resource it finds. It can be run as a separate thread, and pass URIs to another thread us- ing python’s ’Queue’ library. It can also be run in ZMQ push mode,
in which case all URIs are pushed out over a ZMQ socket using push/pull (preferred method). In these threaded cases, chainCrawler will not stop crawling until forced. It will not return duplicate re- sources for over a given, user-definable, refractory period (which can be set to infinite).
