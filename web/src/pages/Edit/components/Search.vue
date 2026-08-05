<template>
  <div class="searchContainer" :class="{ isDark: isDark, show: show, aiScope: searchScope === 'ai' }">
    <div class="closeBtnBox">
      <span class="closeBtn el-icon-close" @click="close"></span>
    </div>
    <div class="searchInputBox">
      <el-input
        ref="searchInputRef"
        :placeholder="searchPlaceholder"
        size="small"
        v-model="searchText"
        @keyup.native="onSearchKeyup"
        @keydown.native.stop
        @focus="onFocus"
        @blur="onBlur"
      >
        <i slot="prefix" class="el-input__icon el-icon-search"></i>
        <el-button
          size="small"
          slot="append"
          v-if="searchScope === 'node' && !isUndef(searchText)"
          @click="showReplaceInput = true"
          >{{ $t('search.replace') }}</el-button
        >
      </el-input>
      <div class="searchNav" v-if="showSearchInfo && !isUndef(searchText) && total > 0">
        <button type="button" class="searchNavBtn" title="上一个 (Shift+Enter)" @mousedown.prevent @click="onSearchPrev">↑</button>
        <button type="button" class="searchNavBtn" title="下一个 (Enter)" @mousedown.prevent @click="onSearchNext">↓</button>
        <span class="searchInfo">{{ currentIndex }} / {{ total }}</span>
      </div>
    </div>
    <el-input
      v-if="showReplaceInput && searchScope === 'node'"
      ref="replaceInputRef"
      :placeholder="$t('search.replacePlaceholder')"
      size="small"
      v-model="replaceText"
      style="margin: 12px 0;"
      @keydown.native.stop
      @focus="onFocus"
      @blur="onBlur"
    >
      <i slot="prefix" class="el-input__icon el-icon-edit"></i>
      <el-button size="small" slot="append" @click="hideReplaceInput">{{
        $t('search.cancel')
      }}</el-button>
    </el-input>
    <div class="btnList" v-if="showReplaceInput && searchScope === 'node'">
      <el-button size="small" :disabled="isReadonly" @click="replace">{{
        $t('search.replace')
      }}</el-button>
      <el-button size="small" :disabled="isReadonly" @click="replaceAll">{{
        $t('search.replaceAll')
      }}</el-button>
    </div>
    <div
      class="searchResultList"
      :style="{ height: searchResultListHeight + 'px' }"
      v-if="showSearchResultList"
    >
      <div
        class="searchResultItem"
        v-for="(item, index) in searchResultList"
        :key="item.id + '-' + index"
        :class="{ active: showSearchInfo && index + 1 === currentIndex }"
        :title="item.name"
        v-html="item.text"
        @click.stop="onSearchResultItemClick(index)"
      ></div>
      <div class="empty" v-if="searchResultList.length <= 0">
        <span class="iconfont iconwushuju"></span>
        <span class="text">{{ $t('search.noResult') }}</span>
      </div>
    </div>
  </div>
</template>

<script>
import { mapState } from 'vuex'
import { isUndef, getTextFromHtml } from 'simple-mind-map/src/utils/index'

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function highlightHtml(text, q) {
  if (!q) return text
  try {
    const reg = new RegExp(escapeRegExp(q), 'gi')
    return text.replace(reg, a => `<mark class="smm-search-result-mark">${a}</mark>`)
  } catch (_) {
    return text
  }
}

export default {
  props: { mindMap: { type: Object } },
  data() {
    return {
      show: false,
      searchText: '',
      replaceText: '',
      showReplaceInput: false,
      currentIndex: 0,
      total: 0,
      showSearchInfo: false,
      searchResultListHeight: 0,
      searchResultList: [],
      showSearchResultList: false,
      searchScope: 'node',
      aiHits: []
    }
  },
  computed: {
    ...mapState({
      isReadonly: state => state.isReadonly,
      isDark: state => state.localConfig.isDark
    }),
    searchPlaceholder() {
      return this.searchScope === 'ai'
        ? (this.$i18n && this.$i18n.locale && this.$i18n.locale.startsWith('zh') ? '搜索当前对话…' : 'Search this chat…')
        : this.$t('search.searchPlaceholder')
    }
  },
  watch: {
    searchText(val) {
      if (isUndef(val)) {
        this.currentIndex = 0
        this.total = 0
        this.showSearchInfo = false
        this.searchResultList = []
        if (this.searchScope === 'node') this.mindMap.search.endSearch()
        else this.clearAiSearch()
        return
      }
      this.runLiveSearch()
    }
  },
  created() {
    this.$bus.$on('show_search', this.showSearch)
    this.mindMap.on('search_info_change', this.handleSearchInfoChange)
    this.mindMap.on('node_click', this.blur)
    this.mindMap.on('draw_click', this.blur)
    this.mindMap.on('expand_btn_click', this.blur)
    this.mindMap.on('search_match_node_list_change', this.onSearchMatchNodeListChange)
    this.mindMap.keyCommand.addShortcut('Control+f', this.onCtrlF)
    window.addEventListener('resize', this.setSearchResultListHeight)
    this.$bus.$on('setData', this.close)
  },
  mounted() { this.setSearchResultListHeight() },
  beforeDestroy() {
    this.$bus.$off('show_search', this.showSearch)
    this.mindMap.off('search_info_change', this.handleSearchInfoChange)
    this.mindMap.off('node_click', this.blur)
    this.mindMap.off('draw_click', this.blur)
    this.mindMap.off('expand_btn_click', this.blur)
    this.mindMap.off('search_match_node_list_change', this.onSearchMatchNodeListChange)
    this.mindMap.keyCommand.removeShortcut('Control+f', this.onCtrlF)
    window.removeEventListener('resize', this.setSearchResultListHeight)
    this.$bus.$off('setData', this.close)
  },
  methods: {
    isUndef,
    aiApi() {
      return window.__aiSearch || null
    },
    resolveSearchScope(payload) {
      if (payload && payload.scope === 'ai') return 'ai'
      if (payload && payload.scope === 'node') return 'node'
      const api = this.aiApi()
      if (api && api.shouldUseAiScope && api.shouldUseAiScope()) return 'ai'
      return 'node'
    },
    handleSearchInfoChange(data) {
      if (this.searchScope !== 'node') return
      this.currentIndex = data.currentIndex + 1
      this.total = data.total
      this.showSearchInfo = true
    },
    onCtrlF() {
      this.showSearch()
    },
    showSearch(payload) {
      const scope = this.resolveSearchScope(payload)
      if (scope === 'ai') this.mindMap.search.endSearch()
      else this.clearAiSearch()
      this.searchScope = scope
      this.$bus.$emit('closeSideBar')
      this.show = true
      this.showSearchResultList = true
      this.$nextTick(() => {
        if (this.$refs.searchInputRef) this.$refs.searchInputRef.focus()
      })
    },
    hideReplaceInput() {
      this.showReplaceInput = false
      this.replaceText = ''
    },
    onFocus() {
      this.mindMap.updateConfig({ enableAutoEnterTextEditWhenKeydown: false })
    },
    onBlur() {
      this.mindMap.updateConfig({ enableAutoEnterTextEditWhenKeydown: true })
    },
    blur() {
      if (this.$refs.searchInputRef) this.$refs.searchInputRef.blur()
      if (this.$refs.replaceInputRef) this.$refs.replaceInputRef.blur()
    },
    onSearchKeyup(e) {
      if (e.key !== 'Enter') return
      e.preventDefault()
      if (e.shiftKey) this.onSearchPrev()
      else this.onSearchNext()
    },
    runLiveSearch() {
      const q = (this.searchText || '').trim()
      if (!q) return
      this.showSearchResultList = true
      if (this.searchScope === 'node') {
        const engine = this.mindMap.search
        engine.isSearching = true
        engine.searchText = this.searchText
        engine.doSearch()
        engine.emitEvent()
      } else {
        this.runAiSearch(q, false)
      }
    },
    onSearchNext() {
      this.showSearchResultList = true
      if (this.searchScope === 'node') {
        this.mindMap.search.search(this.searchText)
      } else {
        this.aiSearchStep(1)
      }
    },
    onSearchPrev() {
      this.showSearchResultList = true
      if (this.searchScope === 'node') {
        const engine = this.mindMap.search
        const total = engine.matchNodeList.length
        if (!total || isUndef(this.searchText)) return
        if (engine.currentIndex < 0 || engine.searchText !== this.searchText) {
          engine.searchText = this.searchText
          engine.doSearch()
          engine.emitEvent()
        }
        const prevIdx = (engine.currentIndex - 1 + total) % total
        engine.jump(prevIdx)
      } else {
        this.aiSearchStep(-1)
      }
    },
    runAiSearch(q, jumpToFirst) {
      const api = this.aiApi()
      if (!api || !api.findAll) return
      this.aiHits = api.findAll(q) || []
      this.total = this.aiHits.length
      this.showSearchInfo = this.total > 0
      this.searchResultList = this.aiHits.map((hit, index) => ({
        id: 'ai-' + index,
        name: hit.name,
        text: highlightHtml(hit.preview, q),
        data: hit
      }))
      if (jumpToFirst && this.aiHits.length) {
        this.currentIndex = 1
        api.jump(0, this.aiHits)
      } else if (!this.aiHits.length) {
        this.currentIndex = 0
      } else if (jumpToFirst === false) {
        this.currentIndex = 0
      }
    },
    aiSearchStep(delta) {
      const q = (this.searchText || '').trim()
      if (!q) return
      if (!this.aiHits.length) {
        this.runAiSearch(q, delta > 0)
        return
      }
      let idx = this.currentIndex - 1
      if (idx < 0) idx = 0
      idx = (idx + delta + this.aiHits.length) % this.aiHits.length
      this.currentIndex = idx + 1
      const api = this.aiApi()
      if (api && api.jump) api.jump(idx, this.aiHits)
    },
    clearAiSearch() {
      const api = this.aiApi()
      if (api && api.clearMarks) api.clearMarks()
      this.aiHits = []
    },
    replace() { this.mindMap.search.replace(this.replaceText, true) },
    replaceAll() { this.mindMap.search.replaceAll(this.replaceText) },
    close() {
      this.show = false
      this.showSearchResultList = false
      this.showSearchInfo = false
      this.total = 0
      this.currentIndex = 0
      this.searchText = ''
      this.aiHits = []
      this.hideReplaceInput()
      this.clearAiSearch()
      this.mindMap.search.endSearch()
    },
    onSearchMatchNodeListChange(list) {
      if (this.searchScope !== 'node') return
      const q = this.searchText.trim()
      this.searchResultList = list.map(item => {
        const data = item.data || item.nodeData.data
        let name = data.text
        const id = data.uid
        if (data.richText) name = getTextFromHtml(name)
        return { data: item, id, text: highlightHtml(name, q), name }
      })
    },
    setSearchResultListHeight() {
      this.searchResultListHeight = window.innerHeight - 267 - 24
    },
    onSearchResultItemClick(index) {
      if (this.searchScope === 'node') {
        this.mindMap.search.jump(index)
      } else {
        this.currentIndex = index + 1
        const api = this.aiApi()
        if (api && api.jump) api.jump(index, this.aiHits)
      }
    }
  }
}
</script>

<style lang="less" scoped>
.searchContainer {
  position: relative;
  background-color: #fff;
  padding: 16px;
  width: 296px;
  border-radius: 12px;
  box-shadow: 0 4px 16px 0 rgba(0, 0, 0, 0.1);
  position: fixed;
  top: 110px;
  right: -296px;
  transition: all 0.3s;
  &.isDark {
    background-color: #363b3f;
    .closeBtnBox { color: #fff; background-color: #363b3f; }
    .searchNavBtn { background: #4a4f54; color: #eee; border-color: #555; }
  }
  &.show { right: 20px; }
  &.aiScope.show { top: 80px; }
  .btnList { display: flex; justify-content: flex-end; }
  .closeBtnBox {
    position: absolute; right: -5px; top: -5px; width: 20px; height: 20px;
    background-color: #fff; border-radius: 50%; display: flex; justify-content: center;
    align-items: center; cursor: pointer; box-shadow: 0 4px 16px 0 rgba(0, 0, 0, 0.1);
    .closeBtn { font-size: 16px; }
  }
  .searchNav {
    display: flex; align-items: center; gap: 4px; margin-top: 8px; justify-content: flex-end;
  }
  .searchNavBtn {
    width: 28px; height: 24px; border: 1px solid #dcdfe6; border-radius: 4px;
    background: #f5f7fa; cursor: pointer; font-size: 14px; line-height: 1; padding: 0;
  }
  .searchNavBtn:hover { background: #ecf5ff; border-color: #b3d8ff; color: #409eff; }
  .searchInfo { color: #909090; font-size: 12px; min-width: 48px; text-align: center; }
  .searchResultList {
    position: absolute; left: 0; top: 100%; width: 100%; background-color: #fff;
    box-shadow: 0 4px 16px 0 rgba(0, 0, 0, 0.1); border-radius: 12px; margin-top: 5px;
    overflow-y: auto; padding: 12px 0;
    .searchResultItem {
      height: 30px; line-height: 30px; white-space: nowrap; overflow: hidden;
      text-overflow: ellipsis; padding: 0 12px; font-size: 14px; cursor: pointer;
      position: relative; padding-left: 22px;
      &::before {
        content: ''; position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
        width: 5px; height: 5px; background-color: #606266; border-radius: 50%;
      }
      &:hover, &.active { background-color: #f2f4f7; }
      &.active::before { background-color: #409eff; }
    }
    .empty {
      width: 100%; height: 100%; display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      .iconfont { font-size: 50px; margin-bottom: 20px; }
      .text { font-size: 14px; color: rgba(26, 26, 26, 0.8); }
    }
  }
}
</style>

<style lang="less">
/* v-html 注入的结果项关键字高亮（不能放在 scoped 里） */
.searchContainer .searchResultItem mark.smm-search-result-mark {
  color: #303133;
  font-weight: 600;
  background: #ffe58f;
  border-radius: 2px;
  padding: 0 2px;
}
.searchContainer.isDark .searchResultItem mark.smm-search-result-mark {
  color: #303133;
  background: #ffc53d;
}
</style>
