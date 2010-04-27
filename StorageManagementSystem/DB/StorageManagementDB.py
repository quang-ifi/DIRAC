########################################################################
# $Header: /tmp/libdirac/tmp.FKduyw2449/dirac/DIRAC3/DIRAC/StorageManagementSystem/DB/StagerDB.py,v 1.3 2009/11/03 16:06:29 acsmith Exp $
########################################################################

""" StorageManagementDB is a front end to the Stager Database.

    There are five tables in the StorageManagementDB: Tasks, CacheReplicas, TaskReplicas, StageRequests.

    The Tasks table is the place holder for the tasks that have requested files to be staged. These can be from different systems and have different associated call back methods.
    The CacheReplicas table keeps the information on all the CacheReplicas in the system. It maps all the file information LFN, PFN, SE to an assigned ReplicaID.
    The TaskReplicas table maps the TaskIDs from the Tasks table to the ReplicaID from the CacheReplicas table.
    The StageRequests table contains each of the prestage request IDs for each of the replicas.
"""

__RCSID__ = "$Id: StagerDB.py,v 1.3 2009/11/03 16:06:29 acsmith Exp $"

from DIRAC                                        import gLogger, gConfig, S_OK, S_ERROR
from DIRAC.Core.Base.DB                           import DB
from DIRAC.Core.Utilities.List                    import intListToString,stringListToString
from DIRAC.Core.Utilities.Time                    import toString
import string,threading

class StorageManagementDB(DB):

  def __init__(self, systemInstance='Default', maxQueueSize=10 ):
    DB.__init__(self,'StorageManagementDB','StorageManagement/StorageManagementDB',maxQueueSize)
    self.lock = threading.Lock()
    self.REPLICAPARAMS = ['ReplicaID','Type','Status','SE','LFN','PFN','Size','FileChecksum','GUID','SubmitTime','LastUpdate','Reason','Links']

  ####################################################################
  #
  # The setRequest method is used to initially insert tasks and their associated files.
  #

  def setRequest(self,lfnDict,source,callbackMethod,sourceTaskID):
    """ This method populates the StagerDB Files and Tasks tables with the requested files.
    """
    # The first step is to create the task in the Tasks table
    res = self._createTask(source,callbackMethod,sourceTaskID)
    if not res['OK']:
      return res
    taskID = res['Value']
    # Get the Replicas which already exist in the CacheReplicas table
    allReplicaIDs = []
    for se,lfns in lfnDict.items():    
      res = self._getExistingReplicas(se,lfns)
      if not res['OK']:
        return res
      existingReplicas = res['Value']
      # Insert the CacheReplicas that do not already exist
      for lfn in lfns:
        if lfn in existingReplicas.keys():
          gLogger.verbose('StagerDB.setRequest: Replica already exists in CacheReplicas table %s @ %s' % (lfn,se))
        else:
          res = self._insertReplicaInformation(lfn,se,'Stage')
          if not res['OK']:
            gLogger.warn("Perform roll back")
          else:
            existingReplicas[lfn] = (res['Value'],'New')
      allReplicaIDs.extend(existingReplicas.values())
    # Insert all the replicas into the TaskReplicas table
    res = self._insertTaskReplicaInformation(taskID,allReplicaIDs)
    if not res['OK']:
      gLogger.error("Perform roll back")
      return res
    return S_OK(taskID)

  def _createTask(self,source,callbackMethod,sourceTaskID):
    """ Enter the task details into the Tasks table """
    self.lock.acquire()
    req = "INSERT INTO Tasks (Source,SubmitTime,CallBackMethod,SourceTaskID) VALUES ('%s',UTC_TIMESTAMP(),'%s','%s');" % (source,callbackMethod,sourceTaskID)
    res = self._update(req)
    self.lock.release()
    if not res['OK']:
      gLogger.error("StagerDB._createTask: Failed to create task.", res['Message'])
      return res
    taskID = res['lastRowId']
    gLogger.info("StagerDB._createTask: Created task with ('%s','%s','%s') and obtained TaskID %s" % (source,callbackMethod,sourceTaskID,taskID))
    return S_OK(taskID)

  def _getExistingReplicas(self,storageElement,lfns):
    """ Obtains the ReplicasIDs for the replicas already entered in the CacheReplicas table """
    req = "SELECT ReplicaID,LFN,Status FROM CacheReplicas WHERE SE = '%s' AND LFN IN (%s);" % (storageElement,stringListToString(lfns))
    res = self._query(req)
    if not res['OK']:
      gLogger.error('StagerDB._getExistingReplicas: Failed to get existing replicas.', res['Message'])
      return res
    existingReplicas = {}
    for replicaID,lfn,status in res['Value']:
      existingReplicas[lfn] = (replicaID,status)
    return S_OK(existingReplicas)

  def _insertReplicaInformation(self,lfn,storageElement,type):
    """ Enter the replica into the CacheReplicas table """
    req = "INSERT INTO CacheReplicas (Type,SE,LFN,PFN,Size,FileChecksum,GUID,SubmitTime,LastUpdate) VALUES ('%s','%s','%s','',0,'','',UTC_TIMESTAMP(),UTC_TIMESTAMP());" % (type,storageElement,lfn)
    res = self._update(req)
    if not res['OK']:
      gLogger.error("_insertReplicaInformation: Failed to insert to CacheReplicas table.",res['Message'])
      return res
    replicaID = res['lastRowId']
    gLogger.verbose("_insertReplicaInformation: Inserted Replica ('%s','%s') and obtained ReplicaID %s" % (lfn,storageElement,replicaID))
    return S_OK(replicaID)

  def _insertTaskReplicaInformation(self,taskID,replicaIDs):
    """ Enter the replicas into TaskReplicas table """
    req = "INSERT INTO TaskReplicas (TaskID,ReplicaID) VALUES "
    for replicaID,status in replicaIDs:
      replicaString = "(%s,%s)," % (taskID,replicaID)
      req = "%s %s" % (req,replicaString)
    req = req.rstrip(',')
    res = self._update(req)
    if not res['OK']:
      gLogger.error('StagerDB._insertTaskReplicaInformation: Failed to insert to TaskReplicas table.',res['Message'])
      return res
    gLogger.info("StagerDB._insertTaskReplicaInformation: Successfully added %s CacheReplicas to Task %s." % (res['Value'],taskID))
    return S_OK()

  ####################################################################

  def __getConnection(self,connection):
    if connection:
      return connection
    res = self._getConnection()
    if res['OK']:
      return res['Value']
    gLogger.warn("Failed to get MySQL connection",res['Message'])
    return connection

  def _getTaskReplicaIDs(self,taskIDs,connection=False):
    req = "SELECT ReplicaID FROM TaskReplicas WHERE TaskID IN (%s);" % intListToString(taskIDs)
    res = self._query(req,connection)
    if not res['OK']:
      return res
    replicaIDs = []
    for tuple in res['Value']:
      replicaIDs.append(tuple[0])
    return S_OK(replicaIDs)

  def getCacheReplicas(self,condDict={}, older=None, newer=None, timeStamp='LastUpdate', orderAttribute=None, limit=None,connection=False):
    """ Get cache replicas for the supplied selection with support for the web standard structure """
    connection = self.__getConnection(connection)
    req = "SELECT %s FROM CacheReplicas" % (intListToString(self.REPLICAPARAMS))
    originalFileIDs = {}
    if condDict or older or newer:
      if condDict.has_key('TaskID'):
        taskIDs = condDict.pop('TaskID')
        if type(taskIDs) not in (ListType,TupleType):
          taskIDs = [taskIDs]
        res = self._getTaskReplicaIDs(taskIDs,connection=connection)
        if not res['OK']:
          return res
        condDict['ReplicaID'] = res['Value']
      req = "%s %s" % (req,self.buildCondition(condDict, older, newer, timeStamp,orderAttribute,limit))
    res = self._query(req,connection)
    if not res['OK']:
      return res
    cacheReplicas = res['Value']
    resultDict = {}
    for row in cacheReplicas:
      resultDict[row[0]] = dict(zip(self.REPLICAPARAMS[1:],row[1:]))
    result = S_OK(resultDict)
    result['Records'] = cacheReplicas
    result['ParameterNames'] = self.REPLICAPARAMS
    return result

  # TODO: Purge
  def getReplicasWithStatus(self,status):
    """ This method retrieves the ReplicaID and LFN from the CacheReplicas table with the supplied Status. """
    return self.getCacheReplicas({'Status':status})

  ####################################################################

  def getTasksWithStatus(self,status):
    """ This method retrieves the TaskID from the Tasks table with the supplied Status. """
    req = "SELECT TaskID,Source,CallBackMethod,SourceTaskID from Tasks WHERE Status = '%s';" % status
    res = self._query(req)
    if not res['OK']:
      return res
    taskIDs = {}
    for taskID,source,callback,sourceTask in res['Value']:
      taskIDs[taskID] = (source,callback,sourceTask)
    return S_OK(taskIDs)

  ####################################################################
  #
  # The state transition of the CacheReplicas from *->Failed
  #

  def updateReplicaFailure(self,terminalReplicaIDs):
    """ This method sets the status to Failure with the failure reason for the supplied Replicas. """
    for replicaID,reason in terminalReplicaIDs.items():
      req = "UPDATE CacheReplicas SET Status = 'Failed',Reason = '%s' WHERE ReplicaID = %s;" % (reason,replicaID)
      res = self._update(req)
      if not res['OK']:
        gLogger.error('StagerDB.updateReplicaFailure: Failed to update replica to failed.',res['Message'])
    return S_OK()

  ####################################################################
  #
  # The state transition of the CacheReplicas from New->Waiting
  #

  def updateReplicaInformation(self,replicaTuples):
    """ This method set the replica size information and pfn for the requested storage element.  """
    for replicaID,pfn,size in replicaTuples:
      req = "UPDATE CacheReplicas SET PFN = '%s', Size = %s, Status = 'Waiting' WHERE ReplicaID = %s and Status != 'Cancelled';" % (pfn,size,replicaID)
      res = self._update(req)
      if not res['OK']:
        gLogger.error('StagerDB.updateReplicaInformation: Failed to insert replica information.', res['Message'])
    return S_OK()

  ####################################################################
  #
  # The state transition of the CacheReplicas from Waiting->StageSubmitted
  #

  def getSubmittedStagePins(self):
    req = "SELECT SE,COUNT(*),SUM(Size) from CacheReplicas WHERE Status NOT IN ('New','Waiting','Failed') GROUP BY SE;"
    res = self._query(req)
    if not res['OK']:
      gLogger.error('StagerDB.getSubmittedStagePins: Failed to obtain submitted requests.',res['Message'])
      return res
    storageRequests = {}
    for storageElement,replicas,totalSize in res['Value']:
      storageRequests[storageElement] = {'Replicas':int(replicas),'TotalSize':int(totalSize)}
    return S_OK(storageRequests)

  def getWaitingReplicas(self):
    req = "SELECT TR.TaskID, R.Status, COUNT(*) from TaskReplicas as TR, CacheReplicas as R where TR.ReplicaID=R.ReplicaID GROUP BY TR.TaskID,R.Status;"
    res = self._query(req)
    if not res['OK']:
      gLogger.error('StagerDB.getWaitingReplicas: Failed to get eligible TaskReplicas',res['Message'])
      return res
    badTasks = []
    goodTasks = []
    for taskID,status,count in res['Value']:
      if taskID in badTasks:
        continue
      elif status in ('New','Failed'):
        badTasks.append(taskID)
      elif status == 'Waiting':
        goodTasks.append(taskID)
    replicas = {}
    if not goodTasks:
      return S_OK(replicas)
    req = "SELECT R.ReplicaID,R.LFN,R.SE,R.Size,R.PFN from CacheReplicas as R, TaskReplicas as TR WHERE R.Status = 'Waiting' AND TR.TaskID in (%s) AND TR.ReplicaID=R.ReplicaID;" % intListToString(goodTasks)
    res = self._query(req)
    if not res['OK']:
      gLogger.error('StagerDB.getWaitingReplicas: Failed to get Waiting replicas',res['Message'])
      return res
    for replicaID,lfn,storageElement,fileSize,pfn in res['Value']:
      replicas[replicaID] = (lfn,storageElement,fileSize,pfn)
    return S_OK(replicas)

  def insertStageRequest(self,requestDict,pinLifeTime):
    req = "INSERT INTO StageRequests (ReplicaID,RequestID,StageRequestSubmitTime,PinLength) VALUES "
    for requestID,replicaIDs in requestDict.items():
      for replicaID in replicaIDs:
        replicaString = "(%s,%s,UTC_TIMESTAMP(),%d)," % (replicaID,requestID,pinLifeTime)
        req = "%s %s" % (req,replicaString)
    req = req.rstrip(',')
    res = self._update(req)
    if not res['OK']:
      gLogger.error('StagerDB.insertStageRequest: Failed to insert to StageRequests table.',res['Message'])
      return res
    gLogger.info("StagerDB.insertStageRequest: Successfully added %s StageRequests with RequestID %s." % (res['Value'],requestID))
    return S_OK()

  ####################################################################
  #
  # The state transition of the CacheReplicas from StageSubmitted->Staged
  #

  def getStageSubmittedReplicas(self):
    req = "SELECT R.ReplicaID,R.SE,R.LFN,R.PFN,R.Size,SR.RequestID from CacheReplicas as R, StageRequests as SR WHERE R.Status = 'StageSubmitted' and R.ReplicaID=SR.ReplicaID;"
    res = self._query(req)
    if not res['OK']:
      gLogger.error('StagerDB.getStageSubmittedReplicas: Failed to obtain submitted requests.',res['Message'])
      return res
    replicas = {}
    for replicaID,storageElement,lfn,pfn,fileSize,requestID in res['Value']:
      replicas[replicaID] = {'LFN':lfn,'StorageElement':storageElement,'PFN':pfn,'Size':fileSize,'RequestID':requestID}
    return S_OK(replicas)

  def setStageComplete(self,replicaIDs):
    req = "UPDATE StageRequests SET StageStatus='Staged',StageRequestCompletedTime = UTC_TIMESTAMP(),PinExpiryTime = DATE_ADD(UTC_TIMESTAMP(),INTERVAL 84000 SECOND) WHERE ReplicaID IN (%s);" % intListToString(replicaIDs)
    res = self._update(req)
    if not res['OK']:
      gLogger.error("StagerDB.setStageComplete: Failed to set StageRequest completed.", res['Message'])
      return res
    return res

  ####################################################################
  #
  # The state transition of the CacheReplicas from Staged->Pinned
  #

  def getStagedReplicas(self):
    req = "SELECT R.ReplicaID, R.LFN, R.SE, R.Size, R.PFN, SR.RequestID FROM CacheReplicas AS R, StageRequests AS SR WHERE R.Status = 'Staged' AND R.ReplicaID=SR.ReplicaID;"
    res = self._query(req)
    if not res['OK']:
      gLogger.error('StagerDB.getStagedReplicas: Failed to get replicas for Staged status',res['Message'])
      return res
    replicas = {}
    for replicaID,lfn,storageElement,fileSize,pfn,requestID in res['Value']:
      replicas[replicaID] = (lfn,storageElement,fileSize,pfn,requestID)
    return S_OK(replicas)

  ####################################################################
  #
  # This code handles the finalization of stage tasks
  #

  def updateStageCompletingTasks(self):
    """ This will select all the Tasks in StageCompleting status and check whether all the associated files are Staged. """
    req = "SELECT TR.TaskID,COUNT(if(R.Status NOT IN ('Staged'),1,NULL)) FROM Tasks AS T, TaskReplicas AS TR, CacheReplicas AS R WHERE T.Status='StageCompleting' AND T.TaskID=TR.TaskID AND TR.ReplicaID=R.ReplicaID GROUP BY TR.TaskID;"
    res = self._query(req)
    if not res['OK']:
      return res
    taskIDs = []
    for taskID,count in res['Value']:
      if int(count) == 0:
        taskIDs.append(taskID)
    if not taskIDs:
      return S_OK(taskIDs)
    req = "UPDATE Tasks SET Status = 'Staged' WHERE TaskID IN (%s);" % intListToString(taskIDs)
    res = self._update(req)
    if not res['OK']:
      return res
    return S_OK(taskIDs)

  def setTasksDone(self,taskIDs):
    """ This will update the status for a list of taskIDs to Done. """
    req = "UPDATE Tasks SET Status = 'Done', CompleteTime = UTC_TIMESTAMP() WHERE TaskID IN (%s);" % intListToString(taskIDs)
    res = self._update(req)
    return res

  def removeTasks(self,taskIDs):
    """ This will delete the entries from the TaskReplicas for the provided taskIDs. """
    req = "DELETE FROM TaskReplicas WHERE TaskID IN (%s);" % intListToString(taskIDs)
    res = self._update(req)
    if not res['OK']:
      return res
    req = "DELETE FROM Tasks WHERE TaskID in (%s);" % intListToString(taskIDs)
    res = self._update(req)
    return res

  def removeUnlinkedReplicas(self):
    """ This will remove from the CacheReplicas tables where there are no associated links. """
    req = "SELECT ReplicaID from CacheReplicas WHERE Links = 0;"
    res = self._query(req)
    if not res['OK']:
      return res
    replicaIDs = []
    for tuple in res['Value']:
      replicaIDs.append(tuple[0])
    if not replicaIDs:
      return S_OK()
    req = "DELETE FROM StageRequests WHERE ReplicaID IN (%s);" % intListToString(replicaIDs)
    res = self._update(req)
    if not res['OK']:
      return res
    req = "DELETE FROM CacheReplicas WHERE ReplicaID IN (%s);" % intListToString(replicaIDs)
    res = self._update(req)
    return res

  ####################################################################
  #
  # This code allows the monitoring of the stage tasks
  #

  def getTaskStatus(self,taskID):
    """ Obtain the task status from the Tasks table. """
    res = self.getTaskInfo(taskID)
    if not res['OK']:
      return res
    taskInfo = res['Value'][taskID]
    return S_OK(taskInfo['Status'])

  def getTaskInfo(self,taskID):
    """ Obtain all the information from the Tasks table for a supplied task. """
    req = "SELECT TaskID,Status,Source,SubmitTime,CompleteTime,CallBackMethod,SourceTaskID from Tasks WHERE TaskID = %s;" % taskID
    res = self._query(req)
    if not res['OK']:
      gLogger.error('StagerDB.getTaskInfo: Failed to get task information.', res['Message'])
      return res
    resDict = {}
    for taskID,status,source,submitTime,completeTime,callBackMethod,sourceTaskID in res['Value']:
      resDict[taskID] = {'Status':status,'Source':source,'SubmitTime':submitTime,'CompleteTime':completeTime,'CallBackMethod':callBackMethod,'SourceTaskID':sourceTaskID}
    if not resDict:
      gLogger.error('StagerDB.getTaskInfo: The supplied task did not exist')
      return S_ERROR('The supplied task did not exist')
    return S_OK(resDict)

  def getTaskSummary(self,taskID):
    """ Obtain the task summary from the database. """
    res = self.getTaskInfo(taskID)
    if not res['OK']:
      return res
    taskInfo = res['Value']
    req = "SELECT R.LFN,R.SE,R.PFN,R.Size,R.Status,R.Reason FROM CacheReplicas AS R, TaskReplicas AS TR WHERE TR.TaskID = %s AND TR.ReplicaID=R.ReplicaID;" % taskID
    res = self._query(req)
    if not res['OK']:
      gLogger.error('StagerDB.getTaskSummary: Failed to get Replica summary for task.',res['Message'])
      return res
    replicaInfo = {}
    for lfn,storageElement,pfn,fileSize,status,reason in res['Value']:
      replicaInfo[lfn] = {'StorageElement':storageElement,'PFN':pfn,'FileSize':fileSize,'Status':status,'Reason':reason}
    resDict = {'TaskInfo':taskInfo,'ReplicaInfo':replicaInfo}
    return S_OK(resDict)
