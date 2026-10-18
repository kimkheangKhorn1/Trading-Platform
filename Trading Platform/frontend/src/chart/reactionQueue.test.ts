import { expect, it, vi } from 'vitest'
import { ReactionQueue, confirmedCandleKey } from './reactionQueue'
import type { BadgeOverlapRequest, OverlapReactionResult } from '../api/badgeOverlap'

const body:BadgeOverlapRequest={feed_id:'f',inputs:{},as_of:1000,timeframes:['M1','M5'],min_timeframes:2,max_ranges:8,chart_timeframe:'M1',lock_id:'i'}
const result:OverlapReactionResult={as_of:1000,reactions:[],warnings:[]}
const flush=async()=>{for(let i=0;i<8;i++) await Promise.resolve()}

it('starts the candle-close request synchronously with no debounce or source-scan dependency',async()=>{
  const fetch=vi.fn(async()=>result),done=vi.fn()
  const queue=new ReactionQueue(fetch,done)
  queue.request({key:'close',scope:'chart',jobs:[{uid:'i',body}]})
  expect(fetch).toHaveBeenCalledTimes(1) // before timers or another event-loop turn
  await flush()
  expect(done).toHaveBeenCalledWith(expect.objectContaining({key:'close'}),{i:result},null)
})

it('keeps only the latest pending close and immediately drains after the current reaction',async()=>{
  let resolve!:(r:OverlapReactionResult)=>void
  const fetch=vi.fn().mockImplementationOnce(()=>new Promise<OverlapReactionResult>(r=>{resolve=r})).mockResolvedValue(result)
  const done=vi.fn(),queue=new ReactionQueue(fetch,done)
  for(const clock of [1000,1060,1120]) queue.request({key:String(clock),scope:'chart',jobs:[{uid:'i',body:{...body,as_of:clock}}]})
  expect(fetch).toHaveBeenCalledTimes(1)
  resolve(result);await flush()
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls[1][0].as_of).toBe(1120)
})

it('requests indicator instances together instead of making one wait for another',async()=>{
  const fetch=vi.fn(()=>new Promise<OverlapReactionResult>(()=>{}))
  const queue=new ReactionQueue(fetch,vi.fn())
  queue.request({key:'close',scope:'chart',jobs:[{uid:'a',body},{uid:'b',body}]})
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('can recover immediately after a reaction endpoint error',async()=>{
  const fetch=vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(result)
  const done=vi.fn(),queue=new ReactionQueue(fetch,done)
  queue.request({key:'first',scope:'chart',jobs:[{uid:'i',body}]});await flush()
  expect(done).toHaveBeenCalledWith(expect.anything(),{},'offline')
  queue.request({key:'retry',scope:'chart',jobs:[{uid:'i',body}]});await flush()
  expect(done).toHaveBeenLastCalledWith(expect.objectContaining({key:'retry'}),{i:result},null)
})


it('detects candle completion immediately even when the observation clock stays the same',()=>{
  const prior:[number,number,number,number,number,number,number]=[840,100,101,99,100,1,1]
  const forming:[number,number,number,number,number,number,number]=[900,100,103,99,102,1,0]
  const before=confirmedCandleKey([prior,forming],960,60)
  expect(before).toBe(JSON.stringify(prior))
  const closed:typeof forming=[...forming];closed[6]=1
  const after=confirmedCandleKey([prior,closed],960,60)
  expect(after).toBe(JSON.stringify(closed))
  expect(after).not.toBe(before)
  expect(confirmedCandleKey([prior,closed],959,60)).toBe(before)
  const corrected:typeof closed=[...closed];corrected[4]=101
  expect(confirmedCandleKey([prior,corrected],960,60)).not.toBe(after)
})
