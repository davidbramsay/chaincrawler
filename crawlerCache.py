from cityhash import CityHash64
import array
from globalConfig import log
import sys



class CrawlerCache(object):

    def __init__(self, mask_length=8):
        '''initializes fixed size hash table (2^mask_length entries), preallocates
        using C for speed and size.  Each stored value in the table is a cityHash64
        value (64 bits), so the hash table can support (theoretically) up to 2^64
        entries.  Defaults to 2^8 entries (256 entries).  The assumption is that
        with a uniform probability distribution, hash collisions while crawling a
        local area of the internet are unlikely.  Instead of storing a linked list
        at each hash table index, we will only store the most recent hash value.
        This may cause us to re-crawl websites, but again it should be fine for
        keeping the crawler from local loops or hyper-local crawl behavior.

        Values are stored based on a bitmask over the 64 bit hash.

        ex:  'http://test.com' hashes to '0x1234567887654321', and the cache table
        size is 2^8, or 256, so we apply an 8 bit mask of 0xff (& 255) to the hash.
        This gives us hashtable[0x21] = 0x1234567887654321.'''

        log.info( "-----------------------------------------------" )
        log.info( "---- Setting up cache ----" )

        self._cache_table_mask_length = mask_length
        self._cache_mask = (2**self._cache_table_mask_length) - 1
        self._cache = array.array('L',(0 for i in range (self._cache_mask+1)))

        if (self._cache.itemsize < 8):
            log.error("Cache Item Size is too small to represent 64 bit CityHash Value")
            raise TypeError("Cache Item Size is too small to represent 64 bit CityHash Value")

        log.info( 'cache length = %s, size = %s kB, mask = b{0:b}'.format(self._cache_mask), \
                len(self._cache), (sys.getsizeof(self._cache)/1000.0) )

        log.info( "-----------------------------------------------" )


    def put(self, uri_string, overwrite=True):
        '''adds a value to the cache.  If overwrite is true, it will overwrite
        an existing value.  If overwrite is False, it will only write the value
        if the cache is empty at that index.  If this makes it fail to write a
        value because a value  already exists at that index, (even if that
        existing value matches its own), it returns False.'''

        hashed_uri = self.hash_uri(uri_string)
        index = hashed_uri & self._cache_mask

        if (self._cache[index] and not overwrite):
            return False
        else:
            self._cache[index] = hashed_uri
            return True


    def put_and_collision(self, uri_string):
        '''adds a value to the cache.  If it is overwriting a different value,
        it returns 'True' to indicate a collision.  Otherwise returns False.'''

        hashed_uri = self.hash_uri(uri_string)
        index = hashed_uri & self._cache_mask

        if (self._cache[index] and self._cache[index] != hashed_uri):
            self._cache[index] = hashed_uri
            return True
        else:
            self._cache[index] = hashed_uri
            return False


    def check(self, uri_string):
        '''returns True if value found in cache, False if not found.'''

        hashed_uri = self.hash_uri(uri_string)
        index = hashed_uri & self._cache_mask

        if (self._cache[index] == hashed_uri):
            return True
        else:
            return False


    def check_and_put(self, uri_string):
        '''If value not in cache, updates cache with value and returns True.
        Otherwise, if the value is already in the table, it returns False.'''

        hashed_uri = self.hash_uri(uri_string)
        index = hashed_uri & self._cache_mask

        if (self._cache[index] != hashed_uri):
            self._cache[index] = hashed_uri
            return True
        else:
            return False


    def clear(self):
        '''clear cache values back to initialized '0' in each location'''
        for index in range(len(self._cache)):
            self._cache[index] = 0


    def size(self):
        '''return number of indices (or values possibly stored) in cache'''
        return len(self._cache)


    @staticmethod
    def hash_uri(uri_string):
        '''broken out 64bit hashing function, so it's easy to replace. Right
        now we use Google's fast crawler hash function CityHash, the 64 bit
        version.  Returns a 64 bit hash based on an input (uri) string.'''
        return CityHash64(uri_string)

