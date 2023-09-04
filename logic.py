from __future__ import annotations

from datetime import timedelta
import heapq
import inspect
from typing import TYPE_CHECKING

from common import config

from datetime import datetime

from model import Model

if TYPE_CHECKING:
    from typing import Optional
    from datetime import date


class SecretLogic:

    def __init__(self, mongo, dt: Optional[date] = None) -> None:
        if dt is None:
            dt = datetime.utcnow().date()
        self.date = dt
        self.mongo = mongo

    async def get_secret(self):
        wv = await self.mongo.find_one({'secret_date': str(self.date)})
        if wv:
            return wv['word']
        else:
            return None

    async def set_secret(self, secret):
        await self.mongo.update_one(
            {'word': secret},
            {'$set': {'secret_date': str(self.date)}}

        )

    async def get_all_secrets(self, with_future: bool):
        date_filter = {'$exists': True, '$ne': None}
        if not with_future:
            date_filter["$lt"] = str(self.date)
        secrets = self.mongo.find({"secret_date": date_filter})
        return ((secret['word'], secret['secret_date']) for secret in await secrets.to_list(None))

    async def get_and_update_solver_count(self):
        secret = await self.mongo.find_one_and_update(
            {'secret_date': str(self.date)}, {'$inc': {'solver_count': 1}}
        )
        return secret.get('solver_count', 0)


class VectorLogic:
    _secret_cache = {}

    def __init__(self, mongo, model: Model, dt):
        self.model = model
        self.mongo = mongo
        self.date = str(dt)
        self.secret_logic = SecretLogic(self.mongo, dt=dt)

    async def get_vector(self, word: str):
        return await self.model.get_vector(word)

    async def get_similarities(self, words: [str]) -> [float]:
        secret_vector = await self.get_secret_vector()
        return self.model.get_similarities(words, secret_vector)

    async def get_secret_vector(self):
        if self._secret_cache.get(self.date) is None:
            self._secret_cache[self.date] = await self.get_vector(
                await self.secret_logic.get_secret()
            )
        return self._secret_cache[self.date]

    async def get_similarity(self, word: str) -> Optional[float]:
        word_vector = await self.get_vector(word)
        if word_vector is None:
            return None
        secret_vector = await self.get_secret_vector()
        return await self.calc_similarity(secret_vector, word_vector)

    async def calc_similarity(self, vec1: [float], vec2: [float]):
        return await self.model.calc_similarity(vec1, vec2)

    async def get_and_update_solver_count(self):
        return await self.secret_logic.get_and_update_solver_count()

    def iterate_all(self):
        return self.model.iterate_all()


class CacheSecretLogic:
    _secret_cache_key_fmt = 'hs:{}:{}'
    _cache_dict = {}
    MAX_CACHE = 50

    def __init__(self, mongo, redis, secret, dt, model):
        self.mongo = mongo
        self.redis = redis
        if dt is None:
            dt = datetime.utcnow().date()
        self.date_ = dt
        self.date = str(dt)
        self.vector_logic = VectorLogic(self.mongo, model=model, dt=dt)
        self.secret = secret
        self._secret_cache_key = None

    @property
    async def secret_cache_key(self):
        if self._secret_cache_key is None:
            if inspect.iscoroutine(self.secret):
                self.secret = await self.secret
            self._secret_cache_key = self._secret_cache_key_fmt.format(self.secret, self.date)
        return self._secret_cache_key

    async def _get_secret_vector(self):
        return await self.vector_logic.get_vector(self.secret)

    def _iterate_all_wv(self):
        return self.vector_logic.iterate_all()

    async def set_secret(self, dry=False, force=False):
        if not force:
            if await self.vector_logic.secret_logic.get_secret() is not None:
                raise ValueError("There is already a secret for this date")

            wv = await self.mongo.find_one({'word': self.secret})
            if wv.get('secret_date') is not None:
                raise ValueError("This word was a secret in the past")

        secret_vec = self._get_secret_vector()

        nearest = []
        async for word, vec in self._iterate_all_wv():
            s = await self.vector_logic.calc_similarity(vec, secret_vec)
            heapq.heappush(nearest, (s, word))
            if len(nearest) > 1000:
                heapq.heappop(nearest)
        nearest.sort()
        self._cache_dict[self.date] = [w[1] for w in nearest]
        if not dry:
            await self.do_populate()

    async def do_populate(self):
        expiration = self.date_ - datetime.utcnow().date() + timedelta(days=4)
        await self.redis.delete(await self.secret_cache_key)
        await self.redis.rpush(await self.secret_cache_key, *await self.cache)
        await self.redis.expire(await self.secret_cache_key, expiration)
        await self.vector_logic.secret_logic.set_secret(self.secret)

    @property
    async def cache(self):
        cache = self._cache_dict.get(self.date)
        if cache is None or len(cache) < 1000:
            if len(self._cache_dict) > self.MAX_CACHE:
                self._cache_dict.clear()
            self._cache_dict[self.date] = await self.redis.lrange(await self.secret_cache_key, 0, -1)
        return self._cache_dict[self.date]

    async def get_cache_score(self, word):
        try:
            return (await self.cache).index(word) + 1
        except ValueError:
            return -1


class CacheSecretLogicGensim(CacheSecretLogic):
    def __init__(self, model_path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import gensim.models.keyedvectors as word2vec
        self.model = word2vec.KeyedVectors.load(model_path).wv
        self.words = self.model.key_to_index.keys()

    def _get_secret_vector(self):
        return self.model[self.secret]


class EasterEggLogic:
    EASTER_EGGS = config.easter_eggs

    @staticmethod
    def get_easter_egg(phrase):
        return EasterEggLogic.EASTER_EGGS.get(phrase)
