import {
  bfsWalk,
  getTextFromHtml,
  isUndef,
  replaceHtmlText,
  formatGetNodeGeneralization
} from '../utils/index'
import MindMapNode from '../core/render/node/MindMapNode'
import { CONSTANTS } from '../constants/constant'

// 搜索插件
class Search {
  //  构造函数
  constructor({ mindMap }) {
    this.mindMap = mindMap
    // 是否正在搜索
    this.isSearching = false
    // 搜索文本
    this.searchText = ''
    // 匹配的节点列表
    this.matchNodeList = []
    // 当前所在的节点列表索引
    this.currentIndex = -1
    // 不要复位搜索文本
    this.notResetSearchText = false
    // 是否自动跳转下一个匹配节点
    this.isJumpNext = false
    this.textHighlightSaved = new Map()
    this.plainTextHighlightSaved = new Map()

    this.bindEvent()
  }

  bindEvent() {
    this.onDataChange = this.onDataChange.bind(this)
    this.onModeChange = this.onModeChange.bind(this)
    this.mindMap.on('data_change', this.onDataChange)
    this.mindMap.on('mode_change', this.onModeChange)
  }

  unBindEvent() {
    this.mindMap.off('data_change', this.onDataChange)
    this.mindMap.off('mode_change', this.onModeChange)
  }

  // 节点数据改变了，需要重新搜索
  onDataChange() {
    if (this.isJumpNext) {
      this.isJumpNext = false
      this.search(this.searchText)
      return
    }
    if (this.notResetSearchText) {
      this.notResetSearchText = false
      return
    }
    this.searchText = ''
  }

  // 监听只读模式切换
  onModeChange(mode) {
    const isReadonly = mode === CONSTANTS.MODE.READONLY
    // 如果是由只读模式切换为非只读模式，需要清除只读模式下的节点高亮
    if (
      !isReadonly &&
      this.isSearching &&
      this.matchNodeList[this.currentIndex]
    ) {
      this.matchNodeList[this.currentIndex].closeHighlight()
    }
  }

  // 搜索
  search(text, callback = () => {}) {
    if (isUndef(text)) return this.endSearch()
    text = String(text)
    this.isSearching = true
    if (this.searchText === text) {
      // 和上一次搜索文本一样，那么搜索下一个
      this.searchNext(callback)
    } else {
      // 和上次搜索文本不一样，那么重新开始
      this.searchText = text
      this.doSearch()
      this.searchNext(callback)
    }
    this.emitEvent()
  }

  // 更新匹配节点列表
  updateMatchNodeList(list) {
    this.matchNodeList = list
    this.mindMap.emit('search_match_node_list_change', list)
  }

  // 结束搜索
  endSearch() {
    if (!this.isSearching) return
    this.clearAllSearchHighlights()
    this.searchText = ''
    this.updateMatchNodeList([])
    this.currentIndex = -1
    this.notResetSearchText = false
    this.isSearching = false
    this.emitEvent()
  }

  resolveNodeInstance(node) {
    if (this.isNodeInstance(node)) return node
    const uid = node.data && node.data.uid
    return uid ? this.mindMap.renderer.findNodeByUid(uid) : null
  }

  clearAllSearchHighlights() {
    this.clearTextHighlights()
    this.matchNodeList.forEach(node => {
      const n = this.resolveNodeInstance(node)
      if (n) {
        n.closeHighlight()
        n.closeSearchMatchHighlight()
      }
    })
  }

  escapeRegExp(text) {
    return String(text).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  }

  getNodeTextEl(nodeInst) {
    try {
      if (!nodeInst._textData || !nodeInst._textData.node) return null
      const fo = nodeInst._textData.node.findOne('foreignObject')
      if (fo && fo.node) {
        return (
          fo.node.querySelector('.smm-richtext-node-wrap') ||
          fo.node.firstElementChild
        )
      }
    } catch (_) {}
    return null
  }

  wrapTextMatches(rootEl, query, isActive) {
    const lowerQ = query.toLowerCase()
    const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT, null)
    const nodes = []
    while (walker.nextNode()) nodes.push(walker.currentNode)
    nodes.forEach(node => {
      const raw = node.nodeValue || ''
      const lower = raw.toLowerCase()
      let idx = lower.indexOf(lowerQ)
      if (idx < 0) return
      const frag = document.createDocumentFragment()
      let pos = 0
      while (idx >= 0) {
        if (idx > pos) frag.appendChild(document.createTextNode(raw.slice(pos, idx)))
        const mark = document.createElement('mark')
        mark.className = 'smm-search-text-mark' + (isActive ? ' active' : '')
        mark.textContent = raw.slice(idx, idx + query.length)
        frag.appendChild(mark)
        pos = idx + query.length
        idx = lower.indexOf(lowerQ, pos)
      }
      if (pos < raw.length) frag.appendChild(document.createTextNode(raw.slice(pos)))
      node.parentNode.replaceChild(frag, node)
    })
  }

  applyPlainTextHighlight(nodeInst, query, isActive) {
    if (!nodeInst._textData || !nodeInst._textData.node) return
    const uid = nodeInst.getData('uid')
    const texts = nodeInst._textData.node.find('.smm-text-node-wrap')
    if (!texts || texts.length === 0) return
    const lowerQ = query.toLowerCase()
    texts.forEach((textNode, index) => {
      const content = textNode.text()
      if (!content || !content.toLowerCase().includes(lowerQ)) return
      const key = uid + ':' + index
      if (!this.plainTextHighlightSaved.has(key)) {
        this.plainTextHighlightSaved.set(key, {
          text: content,
          fill: textNode.attr('fill'),
          fontWeight: textNode.attr('font-weight')
        })
      }
      const reg = new RegExp(this.escapeRegExp(query), 'gi')
      textNode.clear()
      let lastIndex = 0
      let match
      const defaultFill = textNode.attr('fill') || '#333'
      while ((match = reg.exec(content)) !== null) {
        if (match.index > lastIndex) {
          textNode.tspan(content.slice(lastIndex, match.index)).fill(defaultFill)
        }
        textNode
          .tspan(match[0])
          .fill(isActive ? '#d48806' : '#409eff')
          .attr('font-weight', 'bold')
        lastIndex = match.index + match[0].length
      }
      if (lastIndex < content.length) {
        textNode.tspan(content.slice(lastIndex)).fill(defaultFill)
      }
    })
  }

  clearPlainTextHighlight(nodeInst) {
    if (!nodeInst._textData || !nodeInst._textData.node) return
    const uid = nodeInst.getData('uid')
    const texts = nodeInst._textData.node.find('.smm-text-node-wrap')
    if (!texts || texts.length === 0) return
    texts.forEach((textNode, index) => {
      const key = uid + ':' + index
      const saved = this.plainTextHighlightSaved.get(key)
      if (!saved) return
      textNode.clear()
      textNode.text(saved.text).fill(saved.fill || '#333')
      if (saved.fontWeight) textNode.attr('font-weight', saved.fontWeight)
      else textNode.attr('font-weight', null)
    })
  }

  clearTextHighlights() {
    this.textHighlightSaved.forEach((html, uid) => {
      const n = this.mindMap.renderer.findNodeByUid(uid)
      if (!n) return
      const el = this.getNodeTextEl(n)
      if (el) el.innerHTML = html
    })
    this.textHighlightSaved.clear()
    this.plainTextHighlightSaved.forEach((_, key) => {
      const uid = key.split(':')[0]
      const n = this.mindMap.renderer.findNodeByUid(uid)
      if (n) this.clearPlainTextHighlight(n)
    })
    this.plainTextHighlightSaved.clear()
  }

  applyTextHighlights(activeIndex = -1) {
    const query = (this.searchText || '').trim()
    if (!query || !this.isSearching) return
    this.matchNodeList.forEach((node, index) => {
      const n = this.resolveNodeInstance(node)
      if (!n) return
      const uid = n.getData('uid')
      const el = this.getNodeTextEl(n)
      const isActive = index === activeIndex
      if (el) {
        if (!this.textHighlightSaved.has(uid)) {
          this.textHighlightSaved.set(uid, el.innerHTML)
        }
        el.innerHTML = this.textHighlightSaved.get(uid)
        this.wrapTextMatches(el, query, isActive)
        return
      }
      this.applyPlainTextHighlight(n, query, isActive)
    })
  }

  applyMatchHighlights(activeIndex = -1) {
    this.clearAllSearchHighlights()
    this.matchNodeList.forEach((node, index) => {
      const n = this.resolveNodeInstance(node)
      if (!n) return
      if (index === activeIndex) n.highlight()
      else n.searchMatchHighlight()
    })
    this.applyTextHighlights(activeIndex)
  }

  // 搜索匹配的节点
  doSearch() {
    this.clearHighlightOnReadonly()
    this.updateMatchNodeList([])
    this.currentIndex = -1
    const { isOnlySearchCurrentRenderNodes } = this.mindMap.opt
    // 如果要搜索收起来的节点，那么要遍历渲染树而不是节点树
    const tree = isOnlySearchCurrentRenderNodes
      ? this.mindMap.renderer.root
      : this.mindMap.renderer.renderTree
    if (!tree) return
    const matchList = []
    bfsWalk(tree, node => {
      let { richText, text, generalization } = isOnlySearchCurrentRenderNodes
        ? node.getData()
        : node.data
      if (richText) {
        text = getTextFromHtml(text)
      }
      if (text.includes(this.searchText)) {
        matchList.push(node)
      }
      // 概要节点
      const generalizationList = formatGetNodeGeneralization({
        generalization
      })
      generalizationList.forEach(gNode => {
        let { richText, text, uid } = gNode
        if (
          isOnlySearchCurrentRenderNodes &&
          !this.mindMap.renderer.findNodeByUid(uid)
        ) {
          return
        }
        if (richText) {
          text = getTextFromHtml(text)
        }
        if (text.includes(this.searchText)) {
          matchList.push({
            data: gNode
          })
        }
      })
    })
    this.updateMatchNodeList(matchList)
    this.applyMatchHighlights(this.currentIndex)
  }

  // 判断对象是否是节点实例
  isNodeInstance(node) {
    return node instanceof MindMapNode
  }

  // 搜索下一个或指定索引，定位到下一个匹配节点
  searchNext(callback, index) {
    if (!this.isSearching || this.matchNodeList.length <= 0) return
    if (
      index !== undefined &&
      Number.isInteger(index) &&
      index >= 0 &&
      index < this.matchNodeList.length
    ) {
      this.currentIndex = index
    } else {
      if (this.currentIndex < this.matchNodeList.length - 1) {
        this.currentIndex++
      } else {
        this.currentIndex = 0
      }
    }
    const currentNode = this.matchNodeList[this.currentIndex]
    this.notResetSearchText = true
    const uid = this.isNodeInstance(currentNode)
      ? currentNode.getData('uid')
      : currentNode.data.uid
    if (!uid) {
      callback()
      return
    }
    const targetNode = this.mindMap.renderer.findNodeByUid(uid)
    this.mindMap.execCommand('GO_TARGET_NODE', uid, node => {
      if (!this.isNodeInstance(currentNode)) {
        this.matchNodeList[this.currentIndex] = node
        this.updateMatchNodeList(this.matchNodeList)
      }
      callback()
      this.applyMatchHighlights(this.currentIndex)
      // 如果当前节点实例已经存在，则不会触发data_change事件，那么需要手动把标志复位
      if (targetNode) {
        this.notResetSearchText = false
      }
    })
  }

  // 只读模式下清除现有匹配节点的高亮（兼容旧调用）
  clearHighlightOnReadonly() {
    this.clearAllSearchHighlights()
  }

  // 定位到指定搜索结果索引的节点
  jump(index, callback = () => {}) {
    this.searchNext(callback, index)
  }

  // 替换当前节点
  replace(replaceText, jumpNext = false) {
    if (
      replaceText === null ||
      replaceText === undefined ||
      !this.isSearching ||
      this.matchNodeList.length <= 0
    )
      return
    // 自动跳转下一个匹配节点
    this.isJumpNext = jumpNext
    replaceText = String(replaceText)
    let currentNode = this.matchNodeList[this.currentIndex]
    if (!currentNode) return
    // 如果当前搜索文本是替换文本的子串，那么该节点还是符合搜索结果的
    const keep = replaceText.includes(this.searchText)
    const text = this.getReplacedText(currentNode, this.searchText, replaceText)
    this.notResetSearchText = true
    currentNode.setText(text, currentNode.getData('richText'))
    if (keep) {
      this.updateMatchNodeList(this.matchNodeList)
      return
    }
    const newList = this.matchNodeList.filter(node => {
      return currentNode !== node
    })
    this.updateMatchNodeList(newList)
    if (this.currentIndex > this.matchNodeList.length - 1) {
      this.currentIndex = -1
    } else {
      this.currentIndex--
    }
    this.emitEvent()
  }

  // 替换所有
  replaceAll(replaceText) {
    if (
      replaceText === null ||
      replaceText === undefined ||
      !this.isSearching ||
      this.matchNodeList.length <= 0
    )
      return
    replaceText = String(replaceText)
    // 如果当前搜索文本是替换文本的子串，那么该节点还是符合搜索结果的
    const keep = replaceText.includes(this.searchText)
    this.notResetSearchText = true
    this.matchNodeList.forEach(node => {
      const text = this.getReplacedText(node, this.searchText, replaceText)
      if (this.isNodeInstance(node)) {
        const data = {
          text
        }
        this.mindMap.renderer.setNodeDataRender(node, data, true)
      } else {
        node.data.text = text
      }
    })
    this.mindMap.render()
    this.mindMap.command.addHistory()
    if (keep) {
      this.updateMatchNodeList(this.matchNodeList)
    } else {
      this.endSearch()
    }
  }

  // 获取某个节点替换后的文本
  getReplacedText(node, searchText, replaceText) {
    let { richText, text } = this.isNodeInstance(node)
      ? node.getData()
      : node.data
    if (richText) {
      return replaceHtmlText(text, searchText, replaceText)
    } else {
      return text.replace(new RegExp(searchText, 'g'), replaceText)
    }
  }

  // 发送事件
  emitEvent() {
    this.mindMap.emit('search_info_change', {
      currentIndex: this.currentIndex,
      total: this.matchNodeList.length
    })
  }

  // 插件被移除前做的事情
  beforePluginRemove() {
    this.unBindEvent()
  }

  // 插件被卸载前做的事情
  beforePluginDestroy() {
    this.unBindEvent()
  }
}

Search.instanceName = 'search'

export default Search
